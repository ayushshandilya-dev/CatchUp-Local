"""Privacy-preserving logging.

Chat text is never passed to these helpers, and the helper API does not accept
free-form message content. A dedicated audit test asserts that representative
chat text never appears in captured logs.
"""

from __future__ import annotations

import logging
from typing import Any


LOGGER_NAME = "catchup_local"


def get_logger() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def log_event(event: str, **metadata: Any) -> None:
    """Log an operational event using only non-content metadata.

    Metadata values are coerced to ints/bools/enums by the caller; this guard
    rejects long strings that could accidentally carry conversation text.
    """
    safe: dict[str, Any] = {}
    for key, value in metadata.items():
        if isinstance(value, str):
            if len(value) > 64:
                safe[key] = "<redacted>"
            else:
                safe[key] = value
        elif isinstance(value, (int, float, bool)) or value is None:
            safe[key] = value
        else:
            safe[key] = type(value).__name__
    rendered = " ".join(f"{key}={value}" for key, value in safe.items())
    get_logger().info("%s %s", event, rendered)
