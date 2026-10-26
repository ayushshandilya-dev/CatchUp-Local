"""End-to-end briefing pipeline.

Candidate retrieval is deterministic. The local model only triages the
candidates it is shown; validation decides what reaches the briefing. A batch
that fails is marked as untriaged and can only surface high-confidence wording,
never fabricated prose.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from catchup_local.analysis.preprocessing import (
    build_candidates,
    mark_instruction_like,
)
from catchup_local.analysis.reading import compute_reading_metrics
from catchup_local.config import Settings
from catchup_local.llm.ollama import (
    JSONParseError,
    ModelResponseError,
    ModelUnavailable,
    OllamaClient,
    OllamaError,
    OllamaTimeout,
    OllamaUnavailable,
)
from catchup_local.llm.prompts import (
    SYSTEM_PROMPT,
    build_candidate_prompt,
    build_repair_prompt,
    candidate_schema,
)
from catchup_local.parser.model import Message, ParseResult
from catchup_local.schemas.briefing import CandidateBatch, CandidateLabel
from catchup_local.validation.evidence import (
    ValidationOutcome,
    merge_labels,
    rejected_summary,
)


ProgressCallback = Callable[[str, int, int], None]


class PipelineError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class _BatchRun:
    labels: dict[str, CandidateLabel] = field(default_factory=dict)
    calls: int = 0
    error_code: str | None = None
    invalid_labels: int = 0


def _briefing_text(outcome: ValidationOutcome) -> str:
    parts: list[str] = [outcome.summary]
    for items in (outcome.updates, outcome.decisions, outcome.actions, outcome.questions):
        for item in items:
            parts.append(item.text)
            if item.owner and item.owner != "unknown":
                parts.append(item.owner)
            if item.deadline and item.deadline != "unknown":
                parts.append(item.deadline)
    for read in outcome.read_first:
        parts.append(read.get("reason", ""))
    return " ".join(part for part in parts if part)


class BriefingPipeline:
    def __init__(self, client: OllamaClient, settings: Settings):
        self.client = client
        self.settings = settings

    def analyze(
        self,
        parsed: ParseResult,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        started = time.monotonic()
        notify = progress or (lambda *_: None)
        notify("preparing", 0, 1)

        instruction_count = mark_instruction_like(parsed.messages)
        candidates = build_candidates(parsed.messages)
        if not candidates:
            raise PipelineError(
                "no_candidates",
                "No decisions, actions, questions or material updates were detected in this chat.",
            )

        batch_size = max(4, self.settings.candidate_batch_size)
        batches = [
            candidates[index : index + batch_size]
            for index in range(0, len(candidates), batch_size)
        ]
        participants = parsed.stats.participants
        labels: dict[str, CandidateLabel] = {}
        failures: list[str] = []
        calls = 0
        invalid_labels = 0
        notify("analyzing", 0, len(batches))

        for index, batch in enumerate(batches):
            prompt = build_candidate_prompt(batch, index, len(batches))
            try:
                run = self._run_batch(prompt, batch, retry_prompt=True)
            except (OllamaUnavailable, ModelUnavailable, OllamaTimeout) as error:
                raise self._fatal_failure(error.code) from error
            calls += run.calls
            invalid_labels += run.invalid_labels
            if run.error_code:
                failures.append(run.error_code)
                # Untriaged candidates keep their explicit wording but are not
                # allowed to become unsupported claims.
                labels.update(
                    {
                        message.id: CandidateLabel(
                            id=message.id,
                            kind=prior,
                            owner="unknown",
                            deadline="unknown",
                            read=False,
                        )
                        for message, prior in batch
                    }
                )
            else:
                labels.update(run.labels)
            notify("analyzing", index + 1, len(batches))

        if len(failures) == len(batches):
            raise self._fatal_failure(failures[0])

        notify("validating", len(batches), len(batches))
        outcome = merge_labels(candidates, labels, parsed.messages, participants)
        self._finish(outcome, parsed, instruction_count)
        if failures:
            outcome.warnings.append(
                f"{len(failures)} of {len(batches)} candidate batch(es) could not be triaged by "
                "the local model. Items from those batches come only from explicit wording and "
                "were not model-classified."
            )
        if outcome.model_omissions:
            outcome.warnings.append(
                f"{outcome.model_omissions} high-confidence item(s) were not labelled by the model. "
                "They are shown from their explicit wording."
            )
        outcome.summary = _derived_summary(outcome, parsed)

        cited_ids = {
            message_id
            for items in (outcome.updates, outcome.decisions, outcome.actions, outcome.questions)
            for item in items
            for message_id in item.evidence_ids
        }
        cited_ids.update(read["message_id"] for read in outcome.read_first)
        summary_evidence = sorted(cited_ids)[:12]

        reading = compute_reading_metrics(parsed.messages, _briefing_text(outcome), cited_ids)
        duration = time.monotonic() - started
        analysed_messages = sum(1 for message in parsed.messages if not message.is_system)

        briefing = {
            "summary": outcome.summary
            or "No overview was produced. Use the cited items and original messages below.",
            "summary_evidence_ids": summary_evidence,
            "summary_kind": "derived_from_items" if outcome.summary else "unavailable",
            "updates": [item.to_dict() for item in outcome.updates],
            "decisions": [item.to_dict() for item in outcome.decisions],
            "actions": [item.to_dict() for item in outcome.actions],
            "questions": [item.to_dict() for item in outcome.questions],
            "read_first": [
                {
                    "message_id": read["message_id"],
                    "reason": read["reason"],
                }
                for read in outcome.read_first
            ],
            "coverage_notes": [],
            "refused_claims": rejected_summary(outcome.rejected),
            "warnings": outcome.warnings,
            "coverage": {
                "messages_total": parsed.stats.messages,
                "messages_analysed": analysed_messages,
                "candidate_batches_total": len(batches),
                "candidate_batches_failed": len(failures),
                "candidates_total": outcome.candidates_total,
                "candidates_labelled": outcome.candidates_labelled,
                "candidates_skipped": outcome.candidates_skipped,
                "model_omissions": outcome.model_omissions,
                "invalid_labels": invalid_labels,
                "items_rejected": outcome.rejected_count,
                "items_deferred": outcome.deferred_count,
                "citations_checked": outcome.citations_checked,
                "citations_valid": outcome.citations_valid,
                "model": self.settings.model,
                "model_calls": calls,
                "duration_seconds": round(duration, 2),
                "degraded": bool(failures),
                "instruction_like_messages": instruction_count,
            },
        }

        notify("done", len(batches), len(batches))
        return {
            "briefing": briefing,
            "parse": {
                "source_name": parsed.source_name,
                "encoding": parsed.encoding,
                "date_order": parsed.date_order,
                "date_order_ambiguous": parsed.date_order_ambiguous,
                "date_order_note": parsed.date_order_note,
                "assumed_date_order": parsed.assumed_date_order,
                "time_formats": parsed.time_formats,
                "stats": parsed.stats.to_dict(),
                "warnings": [warning.to_dict() for warning in parsed.warnings],
            },
            "messages": [_message_payload(message) for message in parsed.messages],
            "reading": reading.to_dict(),
        }

    def _run_batch(
        self,
        prompt: str,
        batch: list[tuple[Message, str]],
        retry_prompt: bool,
    ) -> _BatchRun:
        calls = 0
        try:
            payload, attempts = self.client.generate_json(
                prompt, candidate_schema(batch), SYSTEM_PROMPT, retries=0
            )
            calls += attempts
            labels, invalid = _labels_from_payload(payload, batch)
            if labels:
                return _BatchRun(labels=labels, calls=calls, invalid_labels=invalid)
            error = JSONParseError("No usable candidate labels were returned.")
        except JSONParseError as exc:
            error = exc
        except (OllamaUnavailable, ModelUnavailable, OllamaTimeout):
            raise
        except (ModelResponseError, OllamaError) as exc:
            error = exc

        if retry_prompt:
            try:
                repair = build_repair_prompt(prompt, str(error))
                payload, attempts = self.client.generate_json(
                    repair, candidate_schema(batch), SYSTEM_PROMPT, retries=0
                )
                calls += attempts
                labels, invalid = _labels_from_payload(payload, batch)
                if labels:
                    return _BatchRun(labels=labels, calls=calls, invalid_labels=invalid)
                return _BatchRun(
                    calls=calls,
                    error_code="invalid_model_output",
                    invalid_labels=invalid,
                )
            except (OllamaUnavailable, ModelUnavailable, OllamaTimeout):
                raise
            except (ModelResponseError, OllamaError):
                return _BatchRun(calls=calls, error_code="invalid_model_output")

        return _BatchRun(calls=calls, error_code="invalid_model_output")

    def _fatal_failure(self, code: str) -> PipelineError:
        if code == "model_unavailable":
            return PipelineError(
                "model_unavailable",
                "The configured local model is not installed. "
                f"Run: ollama pull {self.settings.model}",
            )
        if code == "ollama_unavailable":
            return PipelineError(
                "ollama_unavailable",
                "Ollama is not reachable. Start it with: ollama serve",
            )
        if code == "ollama_timeout":
            return PipelineError(
                "ollama_timeout",
                "Local inference timed out. Try a smaller export or raise CATCHUP_TIMEOUT_SECONDS.",
            )
        return PipelineError(
            "analysis_failed",
            "The local model could not produce a valid briefing. Nothing was fabricated or sent online.",
        )

    def _finish(
        self,
        outcome: ValidationOutcome,
        parsed: ParseResult,
        instruction_count: int,
    ) -> None:
        if parsed.date_order_ambiguous:
            outcome.warnings.append(
                "Date order was ambiguous in this export; timestamps use the assumption shown above."
            )
        if parsed.stats.unparsed_lines:
            outcome.warnings.append(
                f"{parsed.stats.unparsed_lines} line(s) could not be parsed and were preserved as unparsed messages."
            )
        if instruction_count:
            outcome.warnings.append(
                f"{instruction_count} message(s) looked like instructions. They were treated as "
                "quoted content and cannot be the only evidence for a claim."
            )


def _labels_from_payload(
    payload: dict[str, Any],
    batch: list[tuple[Message, str]],
) -> tuple[dict[str, CandidateLabel], int]:
    raw_items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(raw_items, list):
        raise JSONParseError("The response did not contain an items list.")
    batch_ids = {message.id for message, _ in batch}
    labels: dict[str, CandidateLabel] = {}
    invalid = 0
    for raw in raw_items:
        try:
            label = CandidateLabel.model_validate(raw)
        except Exception:
            invalid += 1
            continue
        if label.id not in batch_ids or label.id in labels:
            invalid += 1
            continue
        labels[label.id] = label
    return labels, invalid


def _message_payload(message: Any) -> dict[str, Any]:
    return {
        "message_id": message.id,
        "timestamp": message.display_time,
        "timestamp_raw": message.timestamp_raw,
        "sender": message.sender,
        "text": message.text,
        "source_line": message.source_line,
        "source_line_end": message.source_line_end,
        "is_system": message.is_system,
        "is_deleted": message.is_deleted,
        "is_edited": message.is_edited,
        "media_kind": message.media_kind,
        "parse_error": message.parse_error,
        "instruction_like": message.instruction_like,
    }


def _derived_summary(outcome: ValidationOutcome, parsed: ParseResult) -> str:
    """Build the overview from validated items, never from unverified prose."""
    total = parsed.stats.messages
    people = len(parsed.stats.participants)
    parts = [f"{total} messages from {people} participants."]
    if parsed.stats.first_timestamp and parsed.stats.last_timestamp:
        first = parsed.stats.first_timestamp.strftime("%d %b %H:%M")
        last = parsed.stats.last_timestamp.strftime("%d %b %H:%M")
        parts.append(f"Time span: {first} to {last}.")

    if outcome.decisions:
        top = outcome.decisions[0]
        parts.append(f"{len(outcome.decisions)} decision(s) found, including: {top.text}")
    if outcome.actions:
        top_action = outcome.actions[0]
        detail = top_action.text
        if top_action.owner and top_action.owner != "unknown":
            detail += f" (owner: {top_action.owner}"
            if top_action.deadline and top_action.deadline != "unknown":
                detail += f", deadline: {top_action.deadline}"
            detail += ")"
        parts.append(f"{len(outcome.actions)} action item(s), including: {detail}")
    if outcome.questions:
        parts.append(f"{len(outcome.questions)} open question(s) or disagreement(s).")
    if any(
        item.conflict_group
        for item in outcome.decisions + outcome.actions + outcome.questions + outcome.updates
    ):
        parts.append("A contested topic is preserved below; both versions are shown.")
    if outcome.rejected_count:
        parts.append(f"{outcome.rejected_count} item(s) were discarded by evidence validation.")
    if len(parts) == 1:
        parts.append("No decisions, actions or open questions survived evidence validation.")
    return " ".join(parts)[:900]
