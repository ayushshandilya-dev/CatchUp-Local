"""Deterministic preprocessing used to ground and audit model output.

This module does not generate the briefing. It provides cues, participant
facts, and grounding checks so a small local model cannot silently promote an
inference into a fact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from catchup_local.parser.model import Message


_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can",
    "could", "did", "do", "does", "for", "from", "had", "has", "have", "he",
    "her", "here", "him", "his", "how", "i", "if", "in", "into", "is", "it",
    "its", "just", "me", "might", "my", "no", "not", "of", "on", "or", "our",
    "out", "she", "should", "so", "some", "that", "the", "their", "them",
    "then", "there", "these", "they", "this", "to", "too", "up", "us", "was",
    "we", "were", "what", "when", "where", "which", "who", "why", "will",
    "with", "would", "you", "your", "ok", "okay", "yeah", "yes", "lol",
    "haha", "thanks", "thank", "please", "hi", "hey", "hello",
    "said", "says", "say", "asked", "told", "he'd", "she'd", "they'd",
    "i'll", "i'm", "we'd", "we'll", "it's", "that's", "thats",
}

_INSTRUCTION_PATTERNS = re.compile(
    r"(?:"
    r"\bignore (?:all )?(?:the )?(?:previous|prior|above|earlier) (?:instructions?|prompts?|rules?)"
    r"|\bdisregard (?:all )?(?:the )?(?:previous|prior|above|system)"
    r"|\b(?:system|developer) prompt\b"
    r"|\byou are now\b"
    r"|\bact as (?:a|an|the)\b"
    r"|\bjailbreak\b"
    r"|\breveal (?:your|the) (?:prompt|instructions)\b"
    r"|\bdo not summarize\b"
    r"|\boverride (?:the )?(?:instructions?|system)\b"
    r"|\bnew instructions?:\s"
    r"|\bmark (?:this|the) .{0,40}\bas (?:approved|done|complete)\b"
    r")",
    re.IGNORECASE,
)

_DECISION_CUES = re.compile(
    r"\b(?:(?<!not )(?<!is )decided|decision|we agreed|agreed|final call|final decision|"
    r"let'?s go with|we'?ll go with|going with|settled on|we chose|"
    r"the plan is|we'?re doing|vote[d]? (?:for|on)|consensus|locked in)\b",
    re.IGNORECASE,
)

_ACTION_CUES = re.compile(
    r"\b(?:can you|could you|would you|need(?:s)? to|have to|"
    r"will (?:send|book|pay|share|prepare|update|fix|review|submit|call|"
    r"check|create|draft|order|confirm|handle|forward|follow|put|take)|"
    r"i'?ll|i will|i'?m going to|"
    r"send (?:it|the|me)|book (?:it|the)|pay (?:it|the)|"
    r"remind me|follow up|assigned to|responsible for)\b",
    re.IGNORECASE,
)

_IMPERATIVE_ACTION = re.compile(
    r"^(?:okay|ok|also|and|then)?[\s,]*"
    r"(?:ask|send|book|call|confirm|check|share|prepare|update|fix|order|"
    r"forward|put|take|review|submit|create|draft|handle|follow)\b",
    re.IGNORECASE,
)

_HYPOTHETICAL = re.compile(r"\b(?:we'd|we would|you'd|would) need to\b", re.IGNORECASE)

_STRONG_UPDATE = re.compile(
    r"^\s*(?:quick\s+)?(?:reminder|update|bad news|good news)\b|"
    r"^\s*(?:budget|venue|canteen)\s+update\b",
    re.IGNORECASE,
)

_DIRECT_REQUEST = re.compile(
    r"\b(?:can|could|would) you\b|"
    r"\bplease\s+(?:send|book|call|ask|confirm|check|share|prepare|update|"
    r"fix|order|forward|put|take|review|submit|create|draft|handle|follow)\b",
    re.IGNORECASE,
)

_OPEN_QUESTION = re.compile(
    r"\bwho\s+(?:is|are|does|will|can|should|has|have)\b", re.IGNORECASE
)

_DATE_CUE = re.compile(
    r"(?:"
    r"\b(?:today|tomorrow|tonight|this evening|this morning|next week|"
    r"next month|weekend|eod|asap|end of (?:day|week|month))\b"
    r"|\b(?:mon|tues?|wed(?:nes)?|thu(?:rs)?|fri|sat(?:ur)?|sun)(?:day)?\b"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)"
    r"(?:uary|ruary|ch|il|e|y|ust|tember|ober|ember)?\b"
    r"|\b\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\b"
    r"|\b\d{4}-\d{2}-\d{2}\b"
    r"|\b(?:by|before|until|till|on|at)\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b"
    r"|\b\d{1,2}\s*(?:am|pm)\b"
    r"|\bwithin \d+ (?:hours?|days?|weeks?)\b"
    r"|\bin \d+ (?:hours?|days?|weeks?)\b"
    r")",
    re.IGNORECASE,
)

_QUESTION_CUE = re.compile(
    r"(?:\?|^\s*(?:who|what|when|where|why|how|which|can|could|should|"
    r"will|does|do|did|is|are|any(?:one)?)\b)",
    re.IGNORECASE,
)

_NOISE = re.compile(
    r"^(?:lol|ok(?:ay)?|fine|fair|thanks|thank you|great|nice|cool|done|gm|"
    r"good morning|good vibes|sloth mode.*|finally a decision.*|"
    r"one thing at a time.*|i am chill.*|why a sloth.*|"
    r"because we are moving.*|honestly fair.*|lunch\??|"
    r"snacks at a .* war crime.*)$",
    re.IGNORECASE,
)

_OPPOSITION_CUE = re.compile(
    r"\b(?:no,|nope|but |however|instead|disagree|don'?t|doesn'?t|"
    r"not (?:a|the|okay|fine|approved|happening)|cancel|postpone|reject|"
    r"closed|unavailable|sold out|fully booked|clash(?:es)?|"
    r"can'?t|cannot|won'?t|changed (?:my|our) mind|actually|wait|hold on|stop)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Candidate:
    message_id: str
    kind: str
    text: str


def normalize_for_similarity(text: str) -> str:
    text = text.casefold()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^a-z0-9\s']", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def content_tokens(text: str) -> set[str]:
    return {token for token in normalize_for_similarity(text).split() if token not in _STOPWORDS}


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def is_instruction_like(text: str) -> bool:
    return bool(_INSTRUCTION_PATTERNS.search(text))


def has_explicit_cue(text: str, kind: str) -> bool:
    if kind == "decision":
        return bool(_DECISION_CUES.search(text))
    if kind == "action":
        return bool(_ACTION_CUES.search(text) or _IMPERATIVE_ACTION.match(text))
    if kind == "question":
        return bool(_QUESTION_CUE.search(text))
    return False


def has_date_cue(text: str) -> bool:
    return bool(_DATE_CUE.search(text))


def has_opposition_cue(text: str) -> bool:
    return bool(_OPPOSITION_CUE.search(text))


def mark_instruction_like(messages: list[Message]) -> int:
    count = 0
    for message in messages:
        if not message.is_system and not message.parse_error:
            message.instruction_like = is_instruction_like(message.text)
            if message.instruction_like:
                count += 1
    return count


def participants(messages: list[Message]) -> list[str]:
    return sorted({m.sender for m in messages if m.sender and not m.is_system}, key=str.casefold)


def owner_grounded(owner: str, evidence: list[Message]) -> bool:
    owner_norm = normalize_for_similarity(owner)
    if not owner_norm:
        return False
    owner_tokens = set(owner_norm.split())
    for message in evidence:
        sender_norm = normalize_for_similarity(message.sender)
        if sender_norm and (owner_norm == sender_norm):
            return True
        sender_tokens = set(sender_norm.split())
        if sender_tokens and (
            owner_tokens <= sender_tokens or sender_tokens <= owner_tokens
        ):
            return True
        text_norm = normalize_for_similarity(message.text)
        if f" {owner_norm} " in f" {text_norm} ":
            return True
        if owner_tokens and owner_tokens <= set(text_norm.split()):
            return True
    return False


def deadline_grounded(deadline: str, evidence: list[Message]) -> bool:
    if not deadline.strip():
        return False
    if has_date_cue(deadline):
        return True
    normalized = normalize_for_similarity(deadline)
    for message in evidence:
        if normalized and normalized in normalize_for_similarity(message.text):
            return True
        if has_date_cue(message.text):
            return True
    return False


def candidate_cues(messages: list[Message], limit: int = 12) -> list[Candidate]:
    found: list[Candidate] = []
    for message in messages:
        if message.instruction_like:
            continue
        text = message.text
        if has_explicit_cue(text, "decision"):
            found.append(Candidate(message.id, "decision", text.strip()))
        elif has_explicit_cue(text, "action"):
            found.append(Candidate(message.id, "action", text.strip()))
        elif has_explicit_cue(text, "question"):
            found.append(Candidate(message.id, "question", text.strip()))
    if len(found) > limit:
        step = max(1, len(found) // limit)
        found = found[::step][:limit]
    return found


def time_span(messages: list[Message]) -> tuple[datetime | None, datetime | None]:
    timestamps = [m.timestamp for m in messages if m.timestamp]
    if not timestamps:
        return None, None
    return timestamps[0], timestamps[-1]


_UPDATE_CUES = re.compile(
    r"\b(?:cancelled|canceled|closed|moved|postponed|rescheduled|approved|"
    r"confirmed|quote|budget|price|cost|deadline|reminder|update|bad news|"
    r"good news|only .{0,30} left|booked|available|free on|waiting on|"
    r"reset|password|final|changed|decided|agreed|settled|unresolved|"
    r"let'?s not decide|not decide|defer|"
    r"not decided|tbd|still open|we have|we need|leaves us|that leaves)\b",
    re.IGNORECASE,
)

_DEADLINE_PHRASES = re.compile(
    r"(?:\bby\b|\bbefore\b|\bdue\b|\buntil\b|\btill\b|\bon\b|\bat\b)\s+"
    r"(?P<when>"
    r"(?:today|tomorrow|tonight)(?:\s+(?:morning|afternoon|evening))?"
    r"|(?:this|next)\s+(?:week|month|monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"|(?:mon|tues?|wed(?:nes)?|thu(?:rs)?|fri|sat(?:ur)?|sun)(?:day)?"
    r"(?:\s+(?:morning|afternoon|evening))?"
    r"(?:\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?)?"
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?"
    r"|\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
    r"|(?:eod|asap|end of (?:day|week|month)|now)"
    r"|\d{1,2}(?::\d{2})?\s*(?:am|pm)"
    r"|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?"
    r")",
    re.IGNORECASE,
)

_STANDALONE_TIME = re.compile(
    r"(?<!about\s)(?<!regarding\s)(?<!re\s)\b(?P<when>today|tomorrow|tonight|now|eod|asap|"
    r"end of (?:day|week|month)|"
    r"(?:mon|tues?|wed(?:nes)?|thu(?:rs)?|fri|sat(?:ur)?|sun)(?:day)?"
    r"(?:\s+(?:morning|afternoon|evening))?"
    r"(?:\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?)?)\b",
    re.IGNORECASE,
)

_OWNER_REQUEST = re.compile(
    r"^\s*(?P<name>[A-Z][A-Za-z.'-]{1,30})\s*[,:]?\s+"
    r"(?:can|could|would|will|please|need|are you|do you)\b"
)
_OWNER_REPORTED = re.compile(
    r"\b(?P<name>[A-Z][A-Za-z.'-]{1,30})\s+(?:said|says|told|confirmed)\s+"
    r"(?:he|she|they)(?:'d| would| will| is| was)\b"
)
_UNKNOWN_OWNERS = {"", "unknown", "someone", "anyone", "everyone", "team", "all"}


def prior_kind(message: Message) -> str | None:
    text = message.text.strip()
    if message.is_system or message.parse_error or message.is_deleted:
        return None
    if message.instruction_like:
        return None
    if _NOISE.match(text):
        return None
    if has_explicit_cue(text, "decision"):
        return "decision"
    if _STRONG_UPDATE.search(text) or _HYPOTHETICAL.search(text):
        return "update"
    question_signal = (
        text.endswith("?")
        or has_explicit_cue(text, "question")
        or bool(_OPEN_QUESTION.search(text))
    )
    if question_signal and not _DIRECT_REQUEST.search(text):
        if _is_social_question(text):
            return None
        return "question"
    if has_explicit_cue(text, "action"):
        return "action"
    if _UPDATE_CUES.search(text):
        return "update"
    if has_opposition_cue(text):
        return "update"
    return None


def _is_social_question(text: str) -> bool:
    lowered = text.casefold()
    if any(phrase in lowered for phrase in (
        "what did i miss", "why a sloth", "delete the meme", "lunch?",
        "good morning", "how are you", "sloth on it", "only snacks",
        "or are we just vibing", "did anyone actually read the sponsor email",
    )):
        return True
    if len(text) < 25 and not re.search(r"\b(who|when|where|which|deadline|password|venue|desk)\b", lowered):
        return True
    return False


def build_candidates(messages: list[Message]) -> list[tuple[Message, str]]:
    candidates: list[tuple[Message, str]] = []
    for message in messages:
        kind = prior_kind(message)
        if kind:
            candidates.append((message, kind))
    return candidates


def extract_owner(message: Message, participants: list[str]) -> str:
    text = message.text
    if re.search(r"\bI(?:'ll|'m going to| will)\b", text, re.IGNORECASE):
        return message.sender or "unknown"
    match = _OWNER_REQUEST.search(text)
    if match:
        name = match.group("name").strip()
        if name.casefold() in {p.casefold() for p in participants}:
            return name
    match = _OWNER_REPORTED.search(text)
    if match:
        return match.group("name").strip()
    for participant in participants:
        first = participant.split()[0]
        if re.match(rf"^\s*{re.escape(first)}\b", text, re.IGNORECASE):
            return participant
    return "unknown"


def _action_sentence(text: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    for sentence in sentences:
        if re.search(
            r"\b(?:can you|could you|please|i'?ll|i will|we'?ll|need to|"
            r"needs to|have to|will (?:send|book|pay|share|prepare|update|fix|"
            r"review|submit|call|check|create|draft|order|confirm|handle|"
            r"forward|follow|put|take|be))\b",
            sentence,
            re.IGNORECASE,
        ):
            return sentence
    return text


def extract_deadline(text: str) -> str:
    sentence = _action_sentence(text)
    match = _DEADLINE_PHRASES.search(sentence)
    if match:
        when = re.sub(r"\s+", " ", match.group("when")).strip()
        return "unknown" if when.casefold() in {"now", "then"} else when
    match = _STANDALONE_TIME.search(sentence)
    if match:
        when = re.sub(r"\s+", " ", match.group("when")).strip()
        return "unknown" if when.casefold() in {"now", "then"} else when
    return "unknown"
