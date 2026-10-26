from __future__ import annotations

import pytest

from catchup_local.config import Settings
from catchup_local.llm.pipeline import BriefingPipeline, PipelineError
from catchup_local.parser.parser import parse_export


def test_full_pipeline_returns_evidence_backed_briefing(settings, sample_bytes):
    fake = __import__("tests.conftest", fromlist=["FakeOllama"]).FakeOllama()
    parsed = parse_export(sample_bytes, source_name="sample_chat.txt")
    progress = []
    result = BriefingPipeline(fake, settings).analyze(
        parsed, progress=lambda *args: progress.append(args)
    )
    briefing = result["briefing"]
    assert briefing["summary"]
    assert briefing["summary_kind"] == "derived_from_items"
    assert briefing["decisions"]
    assert briefing["actions"]
    assert briefing["coverage"]["citations_valid"] == briefing["coverage"]["citations_checked"]
    assert briefing["coverage"]["candidate_batches_failed"] == 0
    assert (
        briefing["coverage"]["candidates_labelled"]
        + briefing["coverage"]["candidates_skipped"]
        <= briefing["coverage"]["candidates_total"]
    )
    assert progress[-1][0] == "done"
    message_ids = {message["message_id"] for message in result["messages"]}
    for item in (
        briefing["decisions"] + briefing["actions"] + briefing["questions"] + briefing["updates"]
    ):
        assert set(item["evidence_ids"]) <= message_ids
    assert result["reading"]["briefing_words"] > 0


def test_partial_model_output_degrades_explicitly(settings, sample_bytes):
    fake = __import__("tests.conftest", fromlist=["FakeOllama"]).FakeOllama(mode="partial")
    parsed = parse_export(sample_bytes, source_name="sample_chat.txt")
    result = BriefingPipeline(fake, settings).analyze(parsed)
    briefing = result["briefing"]
    assert briefing["coverage"]["model_omissions"] > 0
    assert any("not labelled" in warning for warning in briefing["warnings"])
    assert briefing["coverage"]["degraded"] is False


def test_invalid_output_everywhere_fails_without_fabricating(settings, sample_bytes):
    fake = __import__("tests.conftest", fromlist=["FakeOllama"]).FakeOllama(mode="empty")
    parsed = parse_export(sample_bytes, source_name="sample_chat.txt")
    with pytest.raises(PipelineError) as caught:
        BriefingPipeline(fake, settings).analyze(parsed)
    assert caught.value.code == "analysis_failed"


def test_ollama_unavailable_is_a_typed_pipeline_error(settings, sample_bytes):
    fake = __import__("tests.conftest", fromlist=["FakeOllama"]).FakeOllama(mode="unavailable")
    parsed = parse_export(sample_bytes, source_name="sample_chat.txt")
    with pytest.raises(PipelineError) as caught:
        BriefingPipeline(fake, settings).analyze(parsed)
    assert caught.value.code == "ollama_unavailable"
