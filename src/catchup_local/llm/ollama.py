"""Minimal Ollama client with loopback-only enforcement.

No SDK is required. The client talks to the local Ollama HTTP API with the
standard library, sends a JSON schema for constrained decoding, and exposes
typed errors so the UI can explain what to do next.
"""

from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

from catchup_local.config import Settings


class OllamaError(Exception):
    code = "ollama_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class OllamaUnavailable(OllamaError):
    code = "ollama_unavailable"


class OllamaTimeout(OllamaError):
    code = "ollama_timeout"


class ModelUnavailable(OllamaError):
    code = "model_unavailable"


class ModelResponseError(OllamaError):
    code = "model_response_error"


class JSONParseError(ModelResponseError):
    code = "invalid_json"


@dataclass(frozen=True)
class ModelInfo:
    name: str
    size_bytes: int = 0
    parameter_size: str = ""


def parse_json_object(raw: str) -> dict[str, Any]:
    """Extract one JSON object from a model response.

    Constrained decoding usually makes this trivial, but small models and
    alternative Ollama versions can still add fences or prose. This parser is
    deliberately bounded and predictable.
    """
    if not raw or not raw.strip():
        raise JSONParseError("The model returned an empty response.")
    text = raw.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = _extract_balanced(text)
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list) and len(parsed) == 1 and isinstance(parsed[0], dict):
        return parsed[0]
    raise JSONParseError("The model response was not a JSON object.")


def _extract_balanced(text: str) -> Any:
    start = text.find("{")
    if start == -1:
        raise JSONParseError("No JSON object was found in the model response.")
    depth = 0
    in_string = False
    escaped = False
    for position in range(start, len(text)):
        char = text[position]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : position + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    repaired = re.sub(r",\s*([}\]])", r"\1", candidate)
                    try:
                        return json.loads(repaired)
                    except json.JSONDecodeError as error:
                        raise JSONParseError("The model response contained malformed JSON.") from error
    raise JSONParseError("The model response contained an incomplete JSON object.")


class OllamaClient:
    def __init__(
        self,
        settings: Settings,
        opener: Callable[..., Any] | None = None,
    ):
        parsed = urlparse(settings.ollama_host)
        if parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("Refusing a non-loopback Ollama endpoint.")
        self.settings = settings
        self._opener = opener or urllib.request.urlopen

    def _request(self, url: str, payload: dict | None, timeout: float) -> dict[str, Any]:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers)
        try:
            with self._opener(request, timeout=timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as error:
            detail = ""
            try:
                detail = error.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            if error.code == 404 and "model" in detail.casefold():
                raise ModelUnavailable(
                    "The configured model is not installed in Ollama. "
                    f"Run: ollama pull {self.settings.model}"
                ) from error
            raise OllamaUnavailable(
                f"Ollama returned HTTP {error.code}. Check that Ollama is running."
            ) from error
        except (urllib.error.URLError, ConnectionError, socket.timeout) as error:
            reason = getattr(error, "reason", error)
            if isinstance(reason, TimeoutError) or isinstance(error, socket.timeout):
                raise OllamaTimeout(
                    "Local inference timed out. Try a smaller chat or raise CATCHUP_TIMEOUT_SECONDS."
                ) from error
            raise OllamaUnavailable(
                "Ollama is not reachable on this machine. Start it with: ollama serve"
            ) from error
        except TimeoutError as error:
            raise OllamaTimeout(
                "Local inference timed out. Try a smaller chat or raise CATCHUP_TIMEOUT_SECONDS."
            ) from error
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ModelResponseError("Ollama returned an unreadable response.") from error

    def list_models(self) -> list[ModelInfo]:
        try:
            payload = self._request(self.settings.tags_url, None, timeout=5.0)
        except OllamaError:
            raise
        models = payload.get("models") or []
        result = []
        for model in models:
            details = model.get("details") or {}
            result.append(
                ModelInfo(
                    name=str(model.get("name", "")),
                    size_bytes=int(model.get("size", 0) or 0),
                    parameter_size=str(details.get("parameter_size", "")),
                )
            )
        return result

    def health(self) -> dict[str, Any]:
        try:
            models = self.list_models()
        except OllamaError as error:
            return {
                "available": False,
                "model_present": False,
                "model": self.settings.model,
                "error": error.message,
                "error_code": error.code,
                "models": [],
            }
        names = [model.name for model in models]
        configured = self.settings.model
        present = configured in names or any(
            name.split(":")[0] == configured.split(":")[0] for name in names
        )
        return {
            "available": True,
            "model_present": present,
            "model": configured,
            "models": [
                {"name": m.name, "size_bytes": m.size_bytes, "parameters": m.parameter_size}
                for m in models
            ],
            "error": None if present else f"Model {configured} is not installed. Run: ollama pull {configured}",
            "error_code": None if present else "model_unavailable",
        }

    def generate_json(
        self,
        prompt: str,
        schema: dict[str, Any] | str,
        system: str,
        retries: int = 0,
    ) -> tuple[dict[str, Any], int]:
        payload = {
            "model": self.settings.model,
            "prompt": prompt,
            "system": system,
            "stream": False,
            "format": schema,
            "options": {
                "temperature": self.settings.temperature,
                "top_p": 0.9,
                "num_ctx": 4096,
                "num_predict": 1400,
            },
            "keep_alive": "10m",
        }
        attempts = 0
        last_error: OllamaError | None = None
        for attempt in range(retries + 1):
            attempts += 1
            try:
                result = self._request(
                    self.settings.generate_url,
                    payload,
                    timeout=self.settings.timeout_seconds,
                )
                raw = result.get("response", "")
                return parse_json_object(raw), attempts
            except JSONParseError as error:
                last_error = error
                if attempt < retries:
                    continue
            except OllamaError as error:
                last_error = error
                if error.code in {"ollama_unavailable", "model_unavailable"}:
                    raise
                if attempt < retries:
                    continue
        raise last_error or ModelResponseError("Local inference failed.")
