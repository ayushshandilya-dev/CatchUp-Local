from __future__ import annotations

import re
from pathlib import Path

import pytest

from catchup_local.config import SAMPLE_CHAT, Settings


class FakeOllama:
    def __init__(self, mode: str = "ok"):
        self.mode = mode
        self.calls = 0

    def health(self):
        return {
            "available": True,
            "model_present": True,
            "model": "fake:1b",
            "models": [],
            "error": None,
            "error_code": None,
        }

    def generate_json(self, prompt, schema, system, retries=0):
        self.calls += 1
        if self.mode == "unavailable":
            from catchup_local.llm.ollama import OllamaUnavailable

            raise OllamaUnavailable("Ollama is not reachable.")
        if self.mode == "empty":
            return {"items": []}, 1
        ids = schema["properties"]["items"]["items"]["properties"]["id"]["enum"]
        if self.mode == "partial":
            ids = ids[: max(1, len(ids) // 2)]
        items = []
        for message_id in ids:
            match = re.search(
                rf"^{message_id} \| suggested (\w+)", prompt, re.MULTILINE
            )
            kind = match.group(1) if match else "skip"
            items.append(
                {
                    "id": message_id,
                    "kind": kind,
                    "owner": "unknown",
                    "deadline": "unknown",
                    "read": False,
                }
            )
        return {"items": items}, 1


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        model="fake:1b",
        ollama_host="http://127.0.0.1:11434",
        timeout_seconds=5.0,
    )


@pytest.fixture
def sample_bytes() -> bytes:
    return SAMPLE_CHAT.read_bytes()
