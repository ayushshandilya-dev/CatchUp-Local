from __future__ import annotations

from catchup_local.analysis.preprocessing import (
    build_candidates,
    extract_deadline,
    extract_owner,
    prior_kind,
)
from catchup_local.parser.parser import parse_export


def test_sample_candidates_cover_material_kinds(sample_bytes):
    parsed = parse_export(sample_bytes, source_name="sample_chat.txt")
    candidates = build_candidates(parsed.messages)
    kinds = {kind for _, kind in candidates}
    assert len(candidates) >= 35
    assert {"decision", "action", "question", "update"} <= kinds
    assert all(prior != "skip" for _, prior in candidates)


def test_owner_and_deadline_grounding_cases(sample_bytes):
    parsed = parse_export(sample_bytes, source_name="sample_chat.txt")
    by_text = {message.text: message for message in parsed.messages}
    message = next(m for text, m in by_text.items() if "Sameer said he'd book" in text)
    assert extract_owner(message, parsed.stats.participants) == "Sameer"
    assert extract_deadline(message.text) == "tomorrow"

    desk = next(m for text, m in by_text.items() if "I'll be at the desk at 8am" in text)
    assert extract_deadline(desk.text) == "8am"


def test_weekday_object_is_not_a_deadline():
    assert extract_deadline("Then someone needs to call the admin office about Sunday") == "unknown"
    assert extract_deadline("Let's meet on Sunday") == "Sunday"


def test_instruction_like_is_not_a_candidate(sample_bytes):
    parsed = parse_export(sample_bytes, source_name="sample_chat.txt")
    candidates = build_candidates(parsed.messages)
    candidate_ids = {message.id for message, _ in candidates}
    assert all(
        message.id not in candidate_ids
        for message in parsed.messages
        if message.instruction_like
    )
