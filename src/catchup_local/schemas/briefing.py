"""Structured model output and public briefing schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


Certainty = Literal["explicit", "interpretation"]
GroundingStatus = Literal["supported", "unsupported", "unknown", "not_applicable"]


class CandidateLabel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(description="Candidate message id, copied from the input.")
    kind: Literal["decision", "action", "question", "update", "skip"] = Field(
        description="Materiality and type; skip means banter or not material."
    )
    owner: str = Field(default="unknown")
    deadline: str = Field(default="unknown")
    read: bool = Field(default=False, description="Worth reading directly?")


class CandidateBatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[CandidateLabel] = Field(default_factory=list)


class EvidenceOut(BaseModel):
    message_id: str
    sender: str
    timestamp: str
    text: str
    source_line: int
    instruction_like: bool = False
    media_kind: str | None = None
    parse_error: str | None = None
    is_system: bool = False
    is_deleted: bool = False
    is_edited: bool = False


class BriefingItemOut(BaseModel):
    id: str
    text: str
    evidence_ids: list[str]
    certainty: Certainty
    owner: str | None = None
    owner_status: GroundingStatus = "not_applicable"
    deadline: str | None = None
    deadline_status: GroundingStatus = "not_applicable"
    conflict_group: str | None = None
    warnings: list[str] = Field(default_factory=list)


class ReadFirstOut(BaseModel):
    message_id: str
    reason: str
    message: EvidenceOut | None = None


class RejectedItemOut(BaseModel):
    kind: str
    reason: str
    count: int = 1


class CoverageOut(BaseModel):
    messages_total: int
    messages_analysed: int
    chunks_total: int
    chunks_failed: int
    items_rejected: int
    items_deferred: int = 0
    citations_checked: int
    citations_valid: int
    model: str
    model_calls: int
    duration_seconds: float
    degraded: bool = False
    instruction_like_messages: int = 0


class BriefingOut(BaseModel):
    summary: str
    summary_evidence_ids: list[str]
    summary_kind: Literal["derived_from_items", "unavailable"]
    updates: list[BriefingItemOut]
    decisions: list[BriefingItemOut]
    actions: list[BriefingItemOut]
    questions: list[BriefingItemOut]
    read_first: list[ReadFirstOut]
    coverage_notes: list[str]
    refused_claims: list[RejectedItemOut]
    warnings: list[str]
    coverage: CoverageOut


class ParseMetaOut(BaseModel):
    source_name: str
    encoding: str
    date_order: str
    date_order_ambiguous: bool
    date_order_note: str
    assumed_date_order: str
    time_formats: list[str]
    stats: dict
    warnings: list[dict]


class ReadingOut(BaseModel):
    total_messages: int
    total_words: int
    briefing_words: int
    cited_message_count: int
    cited_message_words: int
    words_to_read: int
    minutes_for_full_chat: float
    minutes_for_briefing: float
    reduction_percent: float
    words_per_minute: int
    method: str
    cited_ids: list[str]


class BriefingResponse(BaseModel):
    briefing: BriefingOut
    parse: ParseMetaOut
    messages: list[EvidenceOut]
    reading: ReadingOut
