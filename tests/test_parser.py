from __future__ import annotations

from catchup_local.parser.parser import parse_export


def test_sample_export_parses_without_unparsed_lines(sample_bytes):
    parsed = parse_export(sample_bytes, source_name="sample_chat.txt")
    assert parsed.stats.messages > 90
    assert parsed.stats.unparsed_lines == 0
    assert parsed.date_order in {"dmy", "mdy"}
    assert parsed.date_order_ambiguous is False
    assert len(parsed.stats.participants) >= 5
    assert parsed.stats.system_messages >= 1
    assert parsed.stats.media_messages >= 1


def test_message_flags_and_continuation(sample_bytes):
    parsed = parse_export(sample_bytes, source_name="sample_chat.txt")
    texts = [message.text for message in parsed.messages]
    assert any("This message was deleted" in text for text in texts)
    assert any(message.is_edited for message in parsed.messages)
    assert any(message.media_kind == "image" for message in parsed.messages)
    assert any(message.is_system for message in parsed.messages)


def test_multiline_message_is_one_continuation():
    raw = b"21/10/2026, 09:00 - Arjun: hello\nthis is still the same message\n21/10/2026, 09:01 - Priya: hi\n"
    parsed = parse_export(raw, source_name="multiline.txt")
    assert parsed.stats.continuation_lines == 1
    assert parsed.stats.messages == 2
    assert "still the same message" in parsed.messages[0].text


def test_leading_unparsed_line_is_preserved():
    raw = b"this line is stray\n21/10/2026, 09:00 - Arjun: hello\n21/10/2026, 09:01 - Priya: hi\n"
    parsed = parse_export(raw, source_name="broken.txt")
    assert parsed.stats.unparsed_lines == 1
    assert any(message.parse_error for message in parsed.messages)


def test_date_order_ambiguity_is_exposed():
    raw = b"01/02/2026, 09:00 - A: one\n03/02/2026, 09:01 - B: two\n"
    parsed = parse_export(raw, source_name="ambiguous.txt")
    assert parsed.date_order_ambiguous is True
    assert parsed.assumed_date_order in {"dmy", "mdy"}
    assert "assum" in parsed.date_order_note.casefold()
