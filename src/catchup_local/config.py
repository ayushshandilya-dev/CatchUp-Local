"""Runtime configuration.

Only loopback Ollama endpoints are accepted. This is deliberate: the product
must never silently fall back to a cloud model. Raise instead of guessing.
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = PROJECT_ROOT / "examples"
SAMPLE_CHAT = EXAMPLES_DIR / "sample_chat.txt"

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_FILE_LINES = 100_000
MAX_MESSAGES = 20_000
MAX_MESSAGE_CHARS = 4_000
CHUNK_SIZE = 28
CHUNK_OVERLAP = 2
MAX_CHUNK_CHARS = 6_000

READING_WORDS_PER_MINUTE = 200

DEFAULT_MODEL = "qwen2.5:1.5b"
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_TIMEOUT_SECONDS = 240.0

ALLOWED_EXTENSIONS = {".txt", ".text"}


@dataclass(frozen=True)
class Settings:
    model: str
    ollama_host: str
    timeout_seconds: float
    chunk_size: int = CHUNK_SIZE
    chunk_overlap: int = CHUNK_OVERLAP
    max_chunk_chars: int = MAX_CHUNK_CHARS
    candidate_batch_size: int = 12
    temperature: float = 0.0

    @property
    def generate_url(self) -> str:
        return f"{self.ollama_host.rstrip('/')}/api/generate"

    @property
    def tags_url(self) -> str:
        return f"{self.ollama_host.rstrip('/')}/api/tags"


def _is_loopback(host: str) -> bool:
    try:
        hostname = urlparse(host).hostname
    except ValueError:
        return False
    if not hostname:
        return False
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def load_settings() -> Settings:
    host = os.environ.get("CATCHUP_OLLAMA_HOST", DEFAULT_OLLAMA_HOST)
    if not _is_loopback(host):
        raise ValueError(
            "CATCHUP_OLLAMA_HOST must be a loopback address; cloud endpoints are not allowed."
        )
    model = os.environ.get("CATCHUP_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    try:
        timeout = float(os.environ.get("CATCHUP_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))
    except ValueError:
        timeout = DEFAULT_TIMEOUT_SECONDS
    timeout = max(10.0, min(timeout, 1_800.0))
    return Settings(model=model, ollama_host=host, timeout_seconds=timeout)
