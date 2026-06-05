"""
Детерминированный re-check последнего вопроса (без LLM) — wall-clock, простая «буква в слове».
"""
from __future__ import annotations

import re
from typing import Any, Optional

from core.dialogue_recheck_anchor import (
    last_substantive_user_question,
    looks_like_recheck_last_answer,
)
from core.timezone_inference import (
    apply_stated_timezone_to_facts,
    ensure_timezone_in_user_facts,
    format_wall_clock_user_reply,
    infer_timezone_from_facts,
    looks_like_wall_clock_question,
)

_LETTER_IN_WORD_RE = re.compile(
    r"(?ui)"
    r"сколько\s+букв\s+"
    r"(?:[«\"']([а-яёa-z])[»\"']|[«\"']?([а-яёa-z])[«\"']?)\s+"
    r"(?:в\s+)?(?:слове\s+)?"
    r"(?:[«\"']([a-zA-Zа-яё\-]+)[»\"']|([a-zA-Zа-яё\-]+))"
)


def _count_letter_in_word(letter: str, word: str) -> int:
    ch = (letter or "").strip()
    w = (word or "").strip()
    if not ch or not w:
        return 0
    if len(ch) == 1 and ch.isascii() and ch.isalpha():
        return sum(1 for c in w if c.lower() == ch.lower())
    return w.lower().count(ch.lower())


def try_trivia_recheck_answer(question: str) -> Optional[str]:
    m = _LETTER_IN_WORD_RE.search(question or "")
    if not m:
        return None
    letter = m.group(1) or m.group(2) or ""
    word = m.group(3) or m.group(4) or ""
    n = _count_letter_in_word(letter, word)
    if n == 0:
        return (
            f"Ноль. В слове {word} нет буквы {letter.upper()} "
            f"(ни русской, ни латинской {letter.upper()})."
        )
    return f"{n} — столько раз буква {letter.upper()} встречается в слове {word}."


def try_recheck_deterministic_reply(
    user_text: str,
    *,
    recent_dialogue: Any,
    user_facts: dict,
    telegram_message_unix: Optional[int] = None,
) -> Optional[str]:
    if not looks_like_recheck_last_answer(user_text):
        return None
    last_q = last_substantive_user_question(recent_dialogue, skip_current=True)
    if not last_q:
        return None
    if looks_like_wall_clock_question(last_q):
        facts = dict(user_facts or {})
        apply_stated_timezone_to_facts(last_q, facts)
        for row in (recent_dialogue or [])[-8:]:
            if isinstance(row, dict) and str(row.get("role") or "").lower() in ("user", "human", ""):
                apply_stated_timezone_to_facts(row.get("text") or row.get("content") or "", facts)
        ensure_timezone_in_user_facts(facts)
        tz = str(facts.get("timezone") or "").strip() or infer_timezone_from_facts(facts) or None
        return format_wall_clock_user_reply(
            effective_tz=tz,
            telegram_message_unix=telegram_message_unix,
            city=str(facts.get("city") or "").strip() or None,
        )
    trivia = try_trivia_recheck_answer(last_q)
    if trivia:
        return trivia
    return None
