"""Normalized message model shared by the parser, analysis and UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


MediaKind = Literal[
    "image", "video", "audio", "sticker", "gif", "document", "contact", "media"
]


class ParseError(Exception):
    """A user-correctable parse failure with a safe, content-free message."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ParseWarning:
    line: int
    code: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"line": self.line, "code": self.code, "detail": self.detail}


@dataclass
class Message:
    id: str
    index: int
    timestamp: datetime | None
    timestamp_raw: str
    sender: str
    text: str
    source_line: int
    source_line_end: int
    is_system: bool = False
    is_deleted: bool = False
    is_edited: bool = False
    media_kind: MediaKind | None = None
    parse_error: str | None = None
    continuation_lines: int = 0
    instruction_like: bool = False

    @property
    def display_time(self) -> str:
        if self.timestamp is None:
            return "unknown time"
        return self.timestamp.strftime("%Y-%m-%d %H:%M")

    @property
    def is_media(self) -> bool:
        return self.media_kind is not None

    def llm_text(self) -> str:
        """Text as shown to the model. Placeholders stay explicit."""
        text = self.text.strip()
        if self.media_kind and not text:
            return f"[{self.media_kind} omitted]"
        return text

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "index": self.index,
            "timestamp": self.timestamp.isoformat(sep=" ") if self.timestamp else None,
            "display_time": self.display_time,
            "timestamp_raw": self.timestamp_raw,
            "sender": self.sender,
            "text": self.text,
            "source_line": self.source_line,
            "source_line_end": self.source_line_end,
            "is_system": self.is_system,
            "is_deleted": self.is_deleted,
            "is_edited": self.is_edited,
            "media_kind": self.media_kind,
            "parse_error": self.parse_error,
            "instruction_like": self.instruction_like,
        }


@dataclass
class ParseStats:
    total_lines: int = 0
    messages: int = 0
    system_messages: int = 0
    unparsed_lines: int = 0
    continuation_lines: int = 0
    media_messages: int = 0
    participants: list[str] = field(default_factory=list)
    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_lines": self.total_lines,
            "messages": self.messages,
            "system_messages": self.system_messages,
            "unparsed_lines": self.unparsed_lines,
            "continuation_lines": self.continuation_lines,
            "media_messages": self.media_messages,
            "participants": self.participants,
            "first_timestamp": self.first_timestamp.isoformat(sep=" ")
            if self.first_timestamp
            else None,
            "last_timestamp": self.last_timestamp.isoformat(sep=" ")
            if self.last_timestamp
            else None,
        }


@dataclass
class ParseResult:
    messages: list[Message]
    warnings: list[ParseWarning]
    stats: ParseStats
    date_order: str
    date_order_ambiguous: bool
    date_order_note: str
    assumed_date_order: str
    time_formats: list[str]
    source_name: str
    encoding: str

    @property
    def message_index(self) -> dict[str, Message]:
        return {message.id: message for message in self.messages}

    @property
    def content_messages(self) -> list[Message]:
        """Messages eligible for AI analysis (not system / not unparsed)."""
        return [
            m
            for m in self.messages
            if not m.is_system and not m.parse_error
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_name": self.source_name,
            "encoding": self.encoding,
            "date_order": self.date_order,
            "date_order_ambiguous": self.date_order_ambiguous,
            "date_order_note": self.date_order_note,
            "assumed_date_order": self.assumed_date_order,
            "time_formats": self.time_formats,
            "stats": self.stats.to_dict(),
            "warnings": [warning.to_dict() for warning in self.warnings],
            "messages": [message.to_dict() for message in self.messages],
        }
