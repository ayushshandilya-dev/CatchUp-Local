"""Transparent reading-burden measurement.

The metric is deliberately simple and reproducible: count whitespace-separated
words in the source chat, in the briefing prose, and in the unique source
messages cited as evidence. Reading time assumes 200 words per minute. It is a
proxy, not a claim about comprehension speed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from catchup_local.config import READING_WORDS_PER_MINUTE
from catchup_local.parser.model import Message


_WORD = re.compile(r"\S+")


def word_count(text: str) -> int:
    return len(_WORD.findall(text or ""))


@dataclass
class ReadingMetrics:
    total_messages: int
    total_words: int
    briefing_words: int
    cited_message_count: int
    cited_message_words: int
    words_to_read: int
    minutes_for_full_chat: float
    minutes_for_briefing: float
    reduction_percent: float
    words_per_minute: int = READING_WORDS_PER_MINUTE
    method: str = (
        "briefing words + unique cited message words, divided by 200 wpm; "
        "full chat uses all parsed user-message words"
    )
    cited_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "total_messages": self.total_messages,
            "total_words": self.total_words,
            "briefing_words": self.briefing_words,
            "cited_message_count": self.cited_message_count,
            "cited_message_words": self.cited_message_words,
            "words_to_read": self.words_to_read,
            "minutes_for_full_chat": round(self.minutes_for_full_chat, 1),
            "minutes_for_briefing": round(self.minutes_for_briefing, 1),
            "reduction_percent": round(self.reduction_percent, 1),
            "words_per_minute": self.words_per_minute,
            "method": self.method,
            "cited_ids": self.cited_ids,
        }


def compute_reading_metrics(
    messages: list[Message],
    briefing_text: str,
    cited_ids: set[str] | list[str],
) -> ReadingMetrics:
    index = {m.id: m for m in messages}
    unique_ids = sorted(set(cited_ids), key=lambda value: (len(value), value))
    cited_messages = [index[message_id] for message_id in unique_ids if message_id in index]
    content = [m for m in messages if not m.is_system and not m.parse_error]
    total_words = sum(word_count(m.text) for m in content)
    briefing_words = word_count(briefing_text)
    cited_words = sum(word_count(m.text) for m in cited_messages)
    words_to_read = briefing_words + cited_words
    full_minutes = total_words / READING_WORDS_PER_MINUTE if total_words else 0.0
    brief_minutes = words_to_read / READING_WORDS_PER_MINUTE if words_to_read else 0.0
    reduction = ((total_words - words_to_read) / total_words * 100.0) if total_words else 0.0
    return ReadingMetrics(
        total_messages=len(content),
        total_words=total_words,
        briefing_words=briefing_words,
        cited_message_count=len(cited_messages),
        cited_message_words=cited_words,
        words_to_read=words_to_read,
        minutes_for_full_chat=full_minutes,
        minutes_for_briefing=brief_minutes,
        reduction_percent=reduction,
        cited_ids=[m.id for m in cited_messages],
    )
