"""Детекция типовых отказов модели (не ошибка API, но ответ бесполезен)."""
from __future__ import annotations

from typing import Tuple

_REFUSAL_MARKERS: Tuple[str, ...] = (
    "не могу помочь",
    "не могу помоч",
    "не могу ответить",
    "не могу дать",
    "не могу предоставить",
    "я не могу",
    "извините, но я не",
    "извините, но я не могу",
    "sorry, i can't",
    "sorry, i cannot",
    "i can't help",
    "i cannot help",
    "as an ai",
    "как языковая модель",
)


def looks_model_refusal(content: str) -> bool:
    """Короткий шаблонный отказ без полезного содержания."""
    low = (content or "").strip().lower()
    if not low or len(low) > 520:
        return False
    if any(m in low for m in _REFUSAL_MARKERS):
        return True
    if low.startswith("извините") and "не могу" in low and len(low) < 220:
        return True
    return False
