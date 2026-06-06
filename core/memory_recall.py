"""
Memory-Recall Guard — controls when memory-recall is allowed.
Permits memory-recall only when:
  - user makes an explicit recall request ("напомни", "что было раньше")
  - OR last 3 messages contain references to old objects.
Otherwise, disables memory recall for the current turn.
"""

from __future__ import annotations

import logging
import re
from typing import Any, List, Optional

from core.safety_config import memory_recall_guard_enabled

logger = logging.getLogger(__name__)

MEMORY_RECALL_GUARD_VERSION = "1.0.0"

_EXPLICIT_RECALL_TRIGGERS = (
    "напомни",
    "что было раньше",
    "вспомни",
    "помнишь",
    "что ты помнишь",
    "что мы обсуждали",
    "прошлый раз",
    "в прошлый раз",
    "просил запомнить",
    "просила запомнить",
    "забыл",
    "забудешь",
    "запоминал",
    "запомнил",
    "что я просил",
    "что я просила",
    "какое слово",
    "какие слова",
)

_OLD_OBJECT_REFERENCES: re.Pattern = re.compile(
    r"\b(?:тот\s+(?:документ|файл|текст|указ|закон|кодекс|постановление)|"
    r"прошл(?:ый|ая|ое|ые)\s+(?:раз|тема|обсуждени[ея]|диалог|беседа|вопрос)|"
    r"ран(?:ее|ьше)\s+(?:упомянут|обсужд(?:ал|али)|говорил)|"
    r"старый\s+(?:документ|файл|текст)|"
    r"предыдущ(?:ий|ая|ее|ие)\s+(?:сообщени[ея]|документ|файл|запрос)|"
    r"запомненн(?:ый|ая|ое|ые)\s+слов[ао]|"
    r"сохранённ(?:ый|ая|ое|ые)\s+слов[ао])",
    re.IGNORECASE,
)


def _reference_count(messages: List[str]) -> int:
    """Count messages that contain references to old objects."""
    count = 0
    for msg in messages:
        if _OLD_OBJECT_REFERENCES.search(msg or ""):
            count += 1
    return count


def memory_recall_allowed(
    *,
    user_text: str,
    recent_messages: Optional[List[str]] = None,
) -> bool:
    """Check if memory-recall is allowed for this turn.

    Args:
        user_text: current user message
        recent_messages: list of recent user messages (last 3)

    Returns:
        True if recall is allowed, False otherwise.
    """
    if not memory_recall_guard_enabled():
        return True

    low = (user_text or "").strip().lower()

    # Explicit recall request
    for trigger in _EXPLICIT_RECALL_TRIGGERS:
        if trigger in low:
            return True

    # References to old objects in last 3 messages
    if recent_messages:
        last_3 = recent_messages[-3:]
        if _reference_count(last_3) >= 1:
            return True

    return False


def disable_memory_recall_for_turn() -> str:
    """Return a signal string indicating memory recall is disabled."""
    return "memory_recall_disabled"


STATE_VERSION = MEMORY_RECALL_GUARD_VERSION
