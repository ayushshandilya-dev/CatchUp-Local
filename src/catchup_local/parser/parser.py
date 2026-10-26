"""WhatsApp text export parser.

Design rules:
- Prefer preservation over guessing. Lines that cannot be parsed become
  visible ``parse_error`` messages and a content-free warning.
- Timestamps are local wall-clock values. Exports do not carry a timezone, so
  no timezone is invented.
- Date order is detected from the whole file when possible; genuinely
  ambiguous files are flagged and an assumption is exposed.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable

from catchup_local.config import MAX_FILE_LINES, MAX_MESSAGES
from catchup_local.parser.model import (
    Message,
    ParseError,
    ParseResult,
    ParseStats,
    ParseWarning,
)


_TIMESTAMP_LINE = re.compile(
    r"^\[?"
    r"(?P<d1>\d{1,4})(?P<sep>[./-])(?P<d2>\d{1,2})(?P=sep)(?P<d3>\d{1,4}),"
    r"[ \t]+"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?"
    r"(?:[ \t]*(?P<ampm>[AaPp])\.?[ \t]*[Mm]\.?)?"
    r"\]?[ \t]*(?:[-–—][ \t]*)?"
    r"(?P<rest>.*)$"
)

_SENDER_LINE = re.compile(r"^(?P<sender>[^:\n]{1,80}):[ \t]?(?P<text>.*)$")

_SYSTEM_PATTERNS = re.compile(
    r"^(?:"
    r"messages and calls are end-to-end encrypted"
    r"|messages to this chat and calls are now secured"
    r"|you created (?:this )?group"
    r"|.+ created (?:this )?group"
    r"|you were added"
    r"|.+ added .+"
    r"|.+ joined using this group"
    r"|.+ changed the subject"
    r"|.+ changed this group'?s icon"
    r"|.+ changed the group description"
    r"|.+ changed their phone number"
    r"|.+ pinned a message"
    r"|.+ changed the settings"
    r"|.+ is now an admin"
    r"|you'?re now an admin"
    r"|security code changed"
    r"|tap to learn more"
    r"|.+ left$"
    r"|.+ removed .+"
    r")",
    re.IGNORECASE,
)

_MEDIA_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("image", re.compile(r"(?:\bimage|\bphoto) omitted|<\s*media omitted\s*>", re.I)),
    ("video", re.compile(r"\bvideo omitted|video attached", re.I)),
    ("audio", re.compile(r"\baudio omitted|audio attached|voice message omitted", re.I)),
    ("sticker", re.compile(r"\bsticker omitted", re.I)),
    ("gif", re.compile(r"\bgif omitted", re.I)),
    ("contact", re.compile(r"\bcontact card omitted", re.I)),
    ("document", re.compile(r"\bdocument omitted|file attached|\.pdf \(file", re.I)),
)

_DELETED_PATTERN = re.compile(
    r"^(?:this message was deleted|you deleted this message)\.?$", re.I
)
_EDITED_PATTERN = re.compile(
    r"\s*(?:<this message was edited>|\(edited\))$", re.I
)

_CONTROL_TRANSLATION = {
    "\u200e": "",
    "\u200f": "",
    "\u202a": "",
    "\u202b": "",
    "\u202c": "",
    "\u2066": "",
    "\u2067": "",
    "\u2068": "",
    "\u2069": "",
    "\ufeff": "",
}


def _normalize_line(line: str) -> str:
    line = line.replace("\u202f", " ").replace("\u00a0", " ")
    line = line.replace("\u2009", " ").replace("\t", " ")
    for char, replacement in _CONTROL_TRANSLATION.items():
        line = line.replace(char, replacement)
    return line.rstrip()


def _detect_date_order(
    lines: Iterable[str], override: str
) -> tuple[str, bool, str, str]:
    dmy_signal = 0
    mdy_signal = 0
    ambiguous = 0
    iso_seen = 0
    slash_seen = 0
    for line in lines:
        match = _TIMESTAMP_LINE.match(line)
        if not match:
            continue
        first = match.group("d1")
        second = match.group("d2")
        third = match.group("d3")
        if len(first) == 4 or int(first) > 31:
            iso_seen += 1
            continue
        slash_seen += 1
        a, b = int(first), int(second)
        if a > 12:
            dmy_signal += 1
        elif b > 12:
            mdy_signal += 1
        elif a != b:
            ambiguous += 1
        _ = third

    if override in {"dmy", "mdy"}:
        is_ambiguous = ambiguous > 0 and dmy_signal == 0 and mdy_signal == 0
        note = (
            "All numeric dates fit both DD/MM and MM/DD; "
            f"using the requested {'DD/MM' if override == 'dmy' else 'MM/DD'} order."
            if is_ambiguous
            else f"Using the requested {'DD/MM' if override == 'dmy' else 'MM/DD'} order."
        )
        return override, is_ambiguous, note, override

    if dmy_signal and mdy_signal:
        return (
            "dmy",
            True,
            "This export mixes dates that can only be DD/MM with dates that can only be MM/DD. "
            "Using DD/MM and flagging the conflict; check the parsed timestamps.",
            "dmy",
        )
    if mdy_signal and not dmy_signal:
        return "mdy", False, "Detected MM/DD order from day values greater than 12.", "mdy"
    if dmy_signal and not mdy_signal:
        return "dmy", False, "Detected DD/MM order from day values greater than 12.", "dmy"
    if iso_seen and not slash_seen:
        return "iso", False, "Detected ISO (YYYY-MM-DD) dates.", "iso"
    if iso_seen and slash_seen:
        return (
            "dmy",
            ambiguous > 0,
            "This export mixes ISO dates with numeric dates. Numeric dates are read as DD/MM.",
            "dmy",
        )
    if ambiguous:
        return (
            "dmy",
            True,
            "All numeric dates fit both DD/MM and MM/DD (no day above 12). "
            "CatchUp Local assumed DD/MM; switch the date order if that is wrong.",
            "dmy",
        )
    return "unknown", False, "No dated lines were found.", "dmy"


def _parse_timestamp(match: re.Match[str], date_order: str) -> datetime:
    d1 = match.group("d1")
    d2 = match.group("d3")
    d3 = match.group("d2")
    year_token = d2
    raw_first = int(d1)
    raw_third = int(d3)

    if len(d1) == 4 or raw_first > 31:
        year, month, day = raw_first, int(d3), int(d2)
    elif date_order == "mdy":
        month, day, year = raw_first, int(d3), int(year_token)
    else:
        day, month, year = raw_first, int(d3), int(year_token)

    if len(str(year_token)) <= 2:
        year = 2000 + year if year < 70 else 1900 + year

    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    second = int(match.group("second") or 0)
    ampm = (match.group("ampm") or "").upper()
    if ampm:
        if not 1 <= hour <= 12:
            raise ValueError("12-hour clock outside 1..12")
        hour = 0 if hour == 12 and ampm == "A" else 12 if hour == 12 else hour
        if ampm == "P" and hour != 12:
            hour += 12
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        raise ValueError("time outside valid range")
    return datetime(year, month, day, hour, minute, second)


def _detect_media(text: str) -> str | None:
    for kind, pattern in _MEDIA_PATTERNS:
        if pattern.search(text):
            return kind
    return None


def _clean_sender(sender: str) -> str:
    sender = sender.strip().strip("~").strip()
    return re.sub(r"\s+", " ", sender)


def decode_export(raw: bytes) -> tuple[str, str, list[ParseWarning]]:
    if not raw:
        raise ParseError("empty_file", "The file is empty.")
    if b"\x00" in raw[:8192]:
        raise ParseError(
            "binary_file", "This looks like a binary file, not a WhatsApp text export."
        )
    warnings: list[ParseWarning] = []
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
            if encoding in {"cp1252", "latin-1"}:
                warnings.append(
                    ParseWarning(
                        line=0,
                        code="encoding_fallback",
                        detail=f"File was not valid UTF-8; decoded as {encoding}.",
                    )
                )
            return text, ("utf-8" if encoding == "utf-8-sig" else encoding), warnings
        except UnicodeDecodeError:
            continue
    raise ParseError("encoding_error", "The file could not be decoded as text.")


def parse_export(
    raw: bytes,
    source_name: str = "chat.txt",
    date_order: str = "auto",
) -> ParseResult:
    if date_order not in {"auto", "dmy", "mdy"}:
        raise ValueError("date_order must be auto, dmy or mdy")
    text, encoding, warnings = decode_export(raw)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if len(lines) > MAX_FILE_LINES:
        raise ParseError(
            "file_too_large",
            f"The export has more than {MAX_FILE_LINES:,} lines. Split it into smaller files.",
        )

    normalized = [_normalize_line(line) for line in lines]
    order, ambiguous, note, assumed = _detect_date_order(normalized, date_order)
    if ambiguous:
        warnings.append(ParseWarning(line=0, code="ambiguous_date_order", detail=note))

    messages: list[Message] = []
    time_formats: set[str] = set()
    unparsed = 0
    continuations = 0
    total_lines = len(lines)
    truncated_scan = False

    for line_number, line in enumerate(normalized, start=1):
        if not line.strip():
            continue
        match = _TIMESTAMP_LINE.match(line)
        if match:
            try:
                timestamp = _parse_timestamp(match, assumed if order in {"dmy", "mdy"} else "dmy")
            except (ValueError, OverflowError):
                unparsed += 1
                warnings.append(
                    ParseWarning(
                        line=line_number,
                        code="invalid_timestamp",
                        detail="A timestamp-shaped line had an invalid date or time; line preserved.",
                    )
                )
                messages.append(
                    Message(
                        id=f"m{len(messages) + 1:04d}",
                        index=len(messages),
                        timestamp=None,
                        timestamp_raw=match.group(0)[:80],
                        sender="",
                        text=line,
                        source_line=line_number,
                        source_line_end=line_number,
                        parse_error="invalid_timestamp",
                    )
                )
                continue

            time_formats.add("12h" if match.group("ampm") else "24h")
            rest = match.group("rest").strip()
            timestamp_raw = match.group(0)[: len(match.group(0)) - len(match.group("rest"))].strip()

            sender = ""
            body = rest
            sender_match = _SENDER_LINE.match(rest)
            if sender_match:
                sender = _clean_sender(sender_match.group("sender"))
                body = sender_match.group("text")
            is_system = not sender
            is_deleted = bool(_DELETED_PATTERN.match(body.strip()))
            is_edited = bool(_EDITED_PATTERN.search(body))
            media_kind = _detect_media(body)
            if is_system and _SYSTEM_PATTERNS.match(body.strip()):
                is_system = True
            elif is_system:
                is_system = True
            elif _SYSTEM_PATTERNS.match(body.strip()) and not sender:
                is_system = True

            message = Message(
                id=f"m{len(messages) + 1:04d}",
                index=len(messages),
                timestamp=timestamp,
                timestamp_raw=timestamp_raw,
                sender=sender,
                text=body,
                source_line=line_number,
                source_line_end=line_number,
                is_system=is_system,
                is_deleted=is_deleted,
                is_edited=is_edited,
                media_kind=media_kind,
            )
            messages.append(message)
            if len(messages) >= MAX_MESSAGES:
                truncated_scan = True
                warnings.append(
                    ParseWarning(
                        line=line_number,
                        code="message_limit",
                        detail=f"Stopped after {MAX_MESSAGES:,} messages; remaining lines were not read.",
                    )
                )
                break
            continue

        # Continuation of the previous message, or an orphan line.
        if messages:
            previous = messages[-1]
            previous.text = f"{previous.text}\n{line}" if previous.text else line
            previous.source_line_end = line_number
            previous.continuation_lines += 1
            continuations += 1
        else:
            unparsed += 1
            warnings.append(
                ParseWarning(
                    line=line_number,
                    code="orphan_line",
                    detail="Line had no timestamp and no preceding message; preserved as unparsed.",
                )
            )
            messages.append(
                Message(
                    id=f"m{len(messages) + 1:04d}",
                    index=len(messages),
                    timestamp=None,
                    timestamp_raw="",
                    sender="",
                    text=line,
                    source_line=line_number,
                    source_line_end=line_number,
                    parse_error="orphan_line",
                )
            )

    _ = truncated_scan
    if not messages:
        raise ParseError(
            "no_messages",
            "No WhatsApp messages were found. Check that this is an exported .txt chat file.",
        )

    content = [m for m in messages if not m.is_system and not m.parse_error]
    if not content:
        raise ParseError(
            "no_messages",
            "The file contains no user messages after parsing. Nothing to brief.",
        )

    timestamps = [m.timestamp for m in messages if m.timestamp]
    participants = sorted({m.sender for m in content if m.sender}, key=str.casefold)
    stats = ParseStats(
        total_lines=total_lines,
        messages=len([m for m in messages if not m.is_system and not m.parse_error]),
        system_messages=len([m for m in messages if m.is_system]),
        unparsed_lines=unparsed,
        continuation_lines=continuations,
        media_messages=len([m for m in messages if m.media_kind]),
        participants=participants,
        first_timestamp=timestamps[0] if timestamps else None,
        last_timestamp=timestamps[-1] if timestamps else None,
    )

    return ParseResult(
        messages=messages,
        warnings=warnings,
        stats=stats,
        date_order=order,
        date_order_ambiguous=ambiguous,
        date_order_note=note,
        assumed_date_order=assumed,
        time_formats=sorted(time_formats),
        source_name=source_name,
        encoding=encoding,
    )
