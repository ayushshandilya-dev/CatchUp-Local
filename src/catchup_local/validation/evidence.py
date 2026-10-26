"""Evidence validation, grounding and merge logic.

The local model triages candidates; this layer decides what the briefing may
show. Every item is anchored to an existing message from the candidate list.
Owners and deadlines survive only when the cited message states them. Explicit
labels are downgraded when the wording is not explicit. Conflicting items are
preserved rather than reconciled.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from catchup_local.analysis.preprocessing import (
    content_tokens,
    extract_deadline,
    extract_owner,
    has_explicit_cue,
    jaccard,
    normalize_for_similarity,
    owner_grounded,
)
from catchup_local.parser.model import Message
from catchup_local.schemas.briefing import CandidateLabel


_UNKNOWN_VALUES = {
    "", "unknown", "n/a", "na", "none", "null", "tbd", "not specified",
    "not mentioned", "unclear", "unspecified", "someone", "anyone",
    "everyone", "team", "the group",
}

_REJECT_REASONS = {
    "unknown_candidate": "A label was dropped because it did not reference a candidate in its batch.",
    "instruction_only_evidence": "A candidate that looked like an instruction was not accepted as a briefing item.",
    "empty_text": "A labelled candidate had no usable text.",
}

_HEDGE = re.compile(r"\b(?:maybe|might|probably|i think|not sure|perhaps|possibly)\b", re.I)
_IMPERATIVE = re.compile(
    r"^(?:(?:okay|ok|also|and|then)[,\s]+)?(?:please\s+)?"
    r"(?:send|book|call|ask|confirm|check|share|prepare|update|fix|order|"
    r"forward|put|take|review|submit|create|draft|handle|follow)\b",
    re.I,
)
_FIRST_PERSON = re.compile(r"\bI(?:'ll| will| am going to| can| could| should)\b", re.I)
_COMMITMENT = re.compile(r"\bI(?:'ll| will| am going to)\b", re.I)


@dataclass
class ValidatedItem:
    kind: str
    text: str
    evidence_ids: list[str]
    certainty: str
    owner: str | None = None
    owner_status: str = "not_applicable"
    deadline: str | None = None
    deadline_status: str = "not_applicable"
    conflict_group: str | None = None
    warnings: list[str] = field(default_factory=list)
    item_id: str = ""
    read_requested: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.item_id,
            "text": self.text,
            "evidence_ids": self.evidence_ids,
            "certainty": self.certainty,
            "owner": self.owner,
            "owner_status": self.owner_status,
            "deadline": self.deadline,
            "deadline_status": self.deadline_status,
            "conflict_group": self.conflict_group,
            "warnings": self.warnings,
        }


@dataclass
class ValidationOutcome:
    updates: list[ValidatedItem] = field(default_factory=list)
    decisions: list[ValidatedItem] = field(default_factory=list)
    actions: list[ValidatedItem] = field(default_factory=list)
    questions: list[ValidatedItem] = field(default_factory=list)
    read_first: list[dict] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    rejected_count: int = 0
    warnings: list[str] = field(default_factory=list)
    citations_checked: int = 0
    citations_valid: int = 0
    candidates_total: int = 0
    candidates_labelled: int = 0
    candidates_skipped: int = 0
    model_omissions: int = 0
    deferred_count: int = 0


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _is_unknown(value: str | None) -> bool:
    return _clean(value).casefold() in _UNKNOWN_VALUES


def _similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, normalize_for_similarity(left), normalize_for_similarity(right)).ratio()


def _deadline_supported(deadline: str, message: Message) -> bool:
    normalized = normalize_for_similarity(deadline)
    if not normalized:
        return False
    text_norm = normalize_for_similarity(message.text)
    if normalized in text_norm:
        return True
    tokens = {token for token in normalized.split() if len(token) >= 3}
    return bool(tokens) and tokens <= set(text_norm.split())


def reconcile_kind(prior: str, model_kind: str, text: str) -> str:
    if model_kind == "skip":
        return "skip"
    if prior == "decision" and model_kind in {"update", "question"}:
        if has_explicit_cue(text, "decision"):
            return "decision"
    if prior == "action" and model_kind in {"update", "question"}:
        if has_explicit_cue(text, "action"):
            return "action"
    if prior == "question" and model_kind == "action":
        if text.strip().endswith("?") and not re.search(
            r"\b(?:can|could|would) you\b|\bplease\b", text, re.I
        ):
            return "question"
    if prior == "question" and model_kind == "update" and text.strip().endswith("?"):
        return "question"
    if model_kind == "decision" and prior != "decision":
        if not has_explicit_cue(text, "decision"):
            if prior in {"action", "question"} and has_explicit_cue(text, prior):
                return prior
    return model_kind


def _build_item(
    message: Message,
    prior: str,
    label: CandidateLabel,
    participants: list[str],
) -> ValidatedItem | None:
    text = _clean(message.text)[:340]
    if not text:
        return None
    kind = reconcile_kind(prior, label.kind, text)
    if kind == "skip":
        return None
    if kind == "decision" and not has_explicit_cue(text, "decision"):
        corrected = "question" if text.rstrip().endswith("?") else "update"
        item = ValidatedItem(
            kind=corrected,
            text=text,
            evidence_ids=[message.id],
            certainty="interpretation",
            read_requested=bool(label.read),
        )
        item.warnings.append(
            "No explicit decision wording was present; the item is shown as "
            f"{corrected}, not a decision."
        )
        return item

    item = ValidatedItem(
        kind=kind,
        text=text,
        evidence_ids=[message.id],
        certainty="explicit" if has_explicit_cue(text, kind) else "interpretation",
        read_requested=bool(label.read),
    )
    if kind == "update" and _HEDGE.search(text):
        item.certainty = "interpretation"
    if kind != label.kind:
        item.warnings.append(
            f"Type corrected from {label.kind} to {kind} using the message's explicit wording."
        )

    if kind == "action":
        model_owner = "" if _is_unknown(label.owner) else _clean(label.owner)[:80]
        fallback_owner = extract_owner(message, participants)
        model_owner_valid = bool(model_owner) and owner_grounded(model_owner, [message])
        if model_owner_valid and model_owner.casefold() == (message.sender or "").casefold():
            if not _FIRST_PERSON.search(text):
                model_owner_valid = False
            elif _IMPERATIVE.match(text) and not _COMMITMENT.search(text):
                model_owner_valid = False
        if fallback_owner != "unknown" and owner_grounded(fallback_owner, [message]):
            item.owner = fallback_owner
            item.owner_status = "supported"
            if model_owner and model_owner.casefold() != fallback_owner.casefold():
                item.warnings.append(
                    f"Model owner '{model_owner}' was replaced by the owner stated in the message."
                )
        elif model_owner_valid:
            item.owner = model_owner
            item.owner_status = "supported"
        else:
            item.owner = "unknown"
            item.owner_status = "unknown"
            if model_owner:
                item.warnings.append(
                    "Owner was not grounded in the cited message; "
                    "a stated owner or 'unknown' was used instead."
                )

        model_deadline = "" if _is_unknown(label.deadline) else _clean(label.deadline)[:120]
        fallback_deadline = extract_deadline(message.text)
        if fallback_deadline != "unknown" and _deadline_supported(fallback_deadline, message):
            item.deadline = fallback_deadline
            item.deadline_status = "supported"
            if model_deadline and not _deadline_supported(model_deadline, message):
                item.warnings.append(
                    f"Model deadline '{model_deadline}' was replaced by the deadline in the message."
                )
        elif model_deadline and _deadline_supported(model_deadline, message):
            item.deadline = model_deadline
            item.deadline_status = "supported"
        else:
            item.deadline = "unknown"
            item.deadline_status = "unknown"
            if model_deadline:
                item.warnings.append(
                    "Deadline was not grounded in the cited message; "
                    "a stated deadline or 'unknown' was used instead."
                )
    return item


def _is_agreement(text: str) -> bool:
    return bool(re.match(r"^(?:yes|yeah|agreed|ok(?:ay)?|done|fine|sure|works)\b", text.strip(), re.I))


def _try_merge(existing: ValidatedItem, incoming: ValidatedItem, index: dict[str, Message]) -> bool:
    if existing.kind != incoming.kind:
        return False
    if existing.kind == "action":
        for left, right in ((existing.owner, incoming.owner), (existing.deadline, incoming.deadline)):
            if left not in (None, "unknown") and right not in (None, "unknown") and left != right:
                return False
    similarity = _similarity(existing.text, incoming.text)
    left_index = index[existing.evidence_ids[0]].index
    right_index = index[incoming.evidence_ids[0]].index
    adjacent = abs(left_index - right_index) <= 2
    token_overlap = jaccard(content_tokens(existing.text), content_tokens(incoming.text))
    if not (
        similarity >= 0.78
        or (adjacent and token_overlap >= 0.2)
        or (adjacent and existing.kind == "decision" and _is_agreement(incoming.text))
        or (adjacent and incoming.kind == "decision" and _is_agreement(existing.text))
    ):
        return False
    existing.evidence_ids = sorted(set(existing.evidence_ids + incoming.evidence_ids))
    if len(incoming.text) > len(existing.text):
        existing.text = incoming.text
    if incoming.certainty == "explicit":
        existing.certainty = "explicit"
    existing.read_requested = existing.read_requested or incoming.read_requested
    if existing.owner in (None, "unknown") and incoming.owner not in (None, "unknown"):
        existing.owner = incoming.owner
        existing.owner_status = incoming.owner_status
    if existing.deadline in (None, "unknown") and incoming.deadline not in (None, "unknown"):
        existing.deadline = incoming.deadline
        existing.deadline_status = incoming.deadline_status
    for warning in incoming.warnings:
        if warning not in existing.warnings:
            existing.warnings.append(warning)
    return True


def _append_or_merge(
    target: list[ValidatedItem],
    item: ValidatedItem,
    index: dict[str, Message],
) -> None:
    for existing in target:
        if _try_merge(existing, item, index):
            return
    target.append(item)


def merge_labels(
    candidates: list[tuple[Message, str]],
    labels: dict[str, CandidateLabel],
    messages: list[Message],
    participants: list[str],
) -> ValidationOutcome:
    index = {message.id: message for message in messages}
    outcome = ValidationOutcome(candidates_total=len(candidates))
    seen_labels: set[str] = set()

    for message, prior in candidates:
        label = labels.get(message.id)
        if label is None:
            if prior in {"decision", "action"}:
                label = CandidateLabel(
                    id=message.id, kind=prior, owner="unknown", deadline="unknown", read=False
                )
                outcome.model_omissions += 1
                fallback_used = True
            else:
                outcome.candidates_skipped += 1
                continue
        else:
            fallback_used = False

        if message.instruction_like:
            outcome.rejected.append("instruction_only_evidence")
            continue
        if label.id in seen_labels:
            continue
        seen_labels.add(label.id)
        item = _build_item(message, prior, label, participants)
        outcome.citations_checked += 1
        if item is None:
            if label.kind == "skip":
                outcome.candidates_skipped += 1
            else:
                outcome.rejected.append("empty_text")
            continue
        outcome.citations_valid += 1
        outcome.candidates_labelled += 1
        if fallback_used:
            item.warnings.append(
                "The model did not label this high-confidence message; it is shown from its explicit wording."
            )
        target = {
            "decision": outcome.decisions,
            "action": outcome.actions,
            "question": outcome.questions,
            "update": outcome.updates,
        }[item.kind]
        _append_or_merge(target, item, index)

    # Labels that referenced something outside the candidate batch are rejected.
    candidate_ids = {message.id for message, _ in candidates}
    for label_id in labels:
        if label_id not in candidate_ids:
            outcome.rejected.append("unknown_candidate")

    counters = {"d": 0, "a": 0, "u": 0, "q": 0}
    for prefix, items in (
        ("u", outcome.updates),
        ("d", outcome.decisions),
        ("a", outcome.actions),
        ("q", outcome.questions),
    ):
        for item in items:
            counters[prefix] += 1
            item.item_id = f"{prefix}{counters[prefix]}"

    _mark_conflicts(outcome, index)
    _limit_items(outcome, index)
    _select_read_first(outcome, index)
    outcome.rejected_count = len(outcome.rejected)
    return outcome


_VENUE_RE = re.compile(
    r"\b(community hall|library auditorium|library|auditorium|main stage|stage|venue|room)\b",
    re.I,
)
_VENUE_WORDS = {"hall", "library", "auditorium", "stage", "venue", "room"}
_AVAILABLE_RE = re.compile(
    r"\b(?:free|available|booked|confirmed|open|works|going with|"
    r"going ahead|decided|can do|is on)\b",
    re.I,
)
_UNAVAILABLE_RE = re.compile(
    r"\b(?:closed|closes?|closing|unavailable|not available|sold out|fully booked|can'?t|"
    r"cannot|won'?t|not free|doesn'?t work|does not work|unresolved|"
    r"not decided|not settled|still open|still unresolved|no longer)\b",
    re.I,
)


def _venue_tokens(text: str) -> set[str]:
    normalized = normalize_for_similarity(text)
    return {word for word in normalized.split() if word in _VENUE_WORDS}


def _venue_polarity(text: str, venue: str) -> int:
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        sentences_tokens = set(normalize_for_similarity(sentence).split())
        if venue not in sentences_tokens:
            continue
        if _UNAVAILABLE_RE.search(sentence):
            return -1
        if _AVAILABLE_RE.search(sentence):
            return 1
    return 0


def _conflict_venues(left: ValidatedItem, right: ValidatedItem) -> set[str]:
    shared = _venue_tokens(left.text) & _venue_tokens(right.text)
    contradictory = set()
    for venue in shared:
        left_polarity = _venue_polarity(left.text, venue)
        right_polarity = _venue_polarity(right.text, venue)
        if left_polarity and right_polarity and left_polarity != right_polarity:
            contradictory.add(venue)
    return contradictory


def _mark_conflicts(outcome: ValidationOutcome, index: dict[str, Message]) -> None:
    group = 0
    all_items = outcome.decisions + outcome.actions + outcome.updates + outcome.questions
    for left_pos in range(len(all_items)):
        left = all_items[left_pos]
        for right_pos in range(left_pos + 1, len(all_items)):
            right = all_items[right_pos]
            if left.conflict_group and right.conflict_group:
                if left.conflict_group != right.conflict_group:
                    continue
            if not _conflict_venues(left, right):
                continue
            if _similarity(left.text, right.text) >= 0.9:
                continue
            if left.conflict_group:
                label = left.conflict_group
            elif right.conflict_group:
                label = right.conflict_group
            else:
                group += 1
                label = f"c{group}"
            left.conflict_group = label
            right.conflict_group = label
            note = (
                "Possible conflict preserved: these messages disagree about the same "
                "venue or facility. Read both sources; the tool did not choose a winner."
            )
            if note not in left.warnings:
                left.warnings.append(note)
            if note not in right.warnings:
                right.warnings.append(note)
    outcome.deferred_count = 0




_SECTION_LIMITS = {
    "decisions": 5,
    "actions": 12,
    "questions": 7,
    "updates": 10,
}


def _limit_items(outcome: ValidationOutcome, index: dict[str, Message]) -> None:
    deferred = 0
    for name, limit in _SECTION_LIMITS.items():
        items: list[ValidatedItem] = getattr(outcome, name)
        if len(items) <= limit:
            continue

        def priority(item: ValidatedItem) -> tuple[int, ...]:
            first = index.get(item.evidence_ids[0])
            position = first.index if first else 10**9
            text = item.text.casefold()
            numeric = 0 if re.search(r"\d", text) else 1
            topic = 0 if re.search(
                r"\b(?:budget|canteen|quote|catering|poster|date|deadline|wifi|"
                r"password|venue|sunday|saturday|registration|desk|shift|projector|"
                r"admin|deck|sponsor|travel|defer|settled)\b",
                text,
            ) else 1
            if name == "actions":
                request = 0 if _IMPERATIVE.match(item.text) or re.search(
                    r"\b(?:can|could|would) you\b|\bplease\b|\bneeds? to\b", item.text, re.I
                ) else 1
                return (
                    0 if item.conflict_group else 1,
                    numeric,
                    request,
                    0 if item.deadline_status == "supported" else 1,
                    0 if item.owner_status == "supported" else 1,
                    position,
                )
            if name == "questions":
                return (0 if item.conflict_group else 1, topic, position)
            if name == "updates":
                strong = 0 if re.match(
                    r"\s*(?:quick\s+)?(?:update|reminder|bad news|good news|budget update)\b",
                    item.text,
                    re.I,
                ) else 1
                return (0 if item.conflict_group else 1, strong, topic, numeric, position)
            return (
                0 if item.conflict_group else 1,
                0 if item.certainty == "explicit" else 1,
                position,
            )

        ranked = sorted(items, key=priority)
        keep = {id(item) for item in ranked[:limit]}
        keep.update(id(item) for item in items if item.conflict_group)
        kept = [item for item in items if id(item) in keep]
        deferred += len(items) - len(kept)
        setattr(outcome, name, kept)
    outcome.deferred_count = deferred
    if deferred:
        outcome.warnings.append(
            f"{deferred} lower-priority candidate(s) were left out to keep the briefing "
            "focused; open the transcript to review them."
        )

def _select_read_first(outcome: ValidationOutcome, index: dict[str, Message]) -> None:
    all_items = outcome.decisions + outcome.actions + outcome.questions + outcome.updates
    preferred: dict[str, ValidatedItem] = {}
    for item in all_items:
        if not item.conflict_group:
            continue
        message = index.get(item.evidence_ids[0])
        last_index = message.index if message else -1
        is_negative = any(
            _venue_polarity(item.text, venue) == -1 for venue in _venue_tokens(item.text)
        )
        score = (1 if is_negative else 0, last_index)
        current = preferred.get(item.conflict_group)
        if current is None:
            preferred[item.conflict_group] = item
            continue
        current_message = index.get(current.evidence_ids[0])
        current_index = current_message.index if current_message else -1
        current_negative = any(
            _venue_polarity(current.text, venue) == -1 for venue in _venue_tokens(current.text)
        )
        if score > (1 if current_negative else 0, current_index):
            preferred[item.conflict_group] = item

    priority: list[tuple[int, ValidatedItem, str]] = []
    for item in all_items:
        if item.conflict_group:
            priority.append((0, item, "Contested topic; read the supporting messages."))
        elif item.kind == "action" and item.deadline_status == "supported":
            priority.append(
                (1, item, f"Action with a stated deadline ({item.deadline}).")
            )
        elif item.kind == "question":
            priority.append((2, item, "Open question that was not resolved in the chat."))
        elif item.read_requested:
            priority.append((3, item, "Flagged by the local model for direct reading."))
        elif item.kind == "decision":
            priority.append((4, item, "Key decision; verify it against the source."))
    priority.sort(key=lambda row: row[0])
    seen: set[str] = set()
    seen_groups: set[str] = set()
    for _, item, reason in priority:
        if item.conflict_group and item.conflict_group in seen_groups:
            continue
        if item.conflict_group and item is not preferred.get(item.conflict_group):
            continue
        message_id = item.evidence_ids[0]
        if message_id in seen or message_id not in index:
            continue
        seen.add(message_id)
        if item.conflict_group:
            seen_groups.add(item.conflict_group)
        outcome.read_first.append({"message_id": message_id, "reason": reason})
        if len(outcome.read_first) >= 4:
            break


def rejected_summary(reasons: list[str]) -> list[dict]:
    counts: dict[str, int] = {}
    for reason in reasons:
        counts[reason] = counts.get(reason, 0) + 1
    return [
        {
            "kind": "item",
            "reason": _REJECT_REASONS.get(reason, "An item was dropped by evidence validation."),
            "count": count,
        }
        for reason, count in sorted(counts.items())
    ]
