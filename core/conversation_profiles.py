"""
Режимы общения (стиль ответа) — сохраняются в behavior_store как conversation_style.

Не меняют модель OpenRouter; подмешивают короткую директиву в system prompt.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

# slug -> (короткая метка для UI, подробное описание для пользователя, текст для LLM)
_PROFILES: Dict[str, Tuple[str, str, str]] = {
    "balanced": (
        "⚖️ Баланс",
        "Обычный режим: и по-человечески, и по делу, без специальных ограничений.",
        (
            "Стиль ответа: сбалансированный. Будь полезным и понятным; "
            "в бытовом чате не усложняй без запроса; по сложным темам можно развернуться."
        ),
    ),
    "easy": (
        "☕ Простой чат",
        "Как живой собеседник: без занудства и «всезнайки», без поучений в обычной болтовне. "
        "По сложным темам (наука, расчёты, техника) всё равно отвечай чётко — если пользователь явно копает глубоко.",
        (
            "Стиль: простой живой разговор. Не говори с позиции «я умнее всех» и не превращай бытовой чат в лекцию. "
            "Не перегружай ответ лишней терминологией, если пользователь об этом не просил. "
            "Если тема явно серьёзная/техническая/научная — сохраняй точность и глубину, можно формулы и пошаговую логику."
        ),
    ),
    "expert": (
        "🔬 Умные темы",
        "Развёрнуто, структурно, можно формулы и строгую логику — когда важна глубина.",
        (
            "Стиль: развёрнутый экспертный ответ. Структурируй (списки, шаги), будь точным; "
            "допустимы формулы и технические детали, если они помогают. "
            "Не смотри свысока на пользователя — объясняй ясно."
        ),
    ),
    "brief": (
        "✂️ Коротко",
        "В основном 1–3 коротких предложения, только суть — если не нужен развёрнутый разбор.",
        (
            "Стиль: максимально кратко. По умолчанию 1–3 коротких предложения с сутью. "
            "Если пользователь явно просит подробно — тогда развернись."
        ),
    ),
    "warm": (
        "🤝 Теплее",
        "Больше эмпатии и мягкого тона; без холодной «канцелярита».",
        (
            "Стиль: тёплый и поддерживающий тон, больше эмпатии. Избегай сухого канцелярита; "
            "не растягивай ответ без нужды."
        ),
    ),
}

DEFAULT_STYLE = "balanced"

VALID_STYLES: frozenset = frozenset(_PROFILES.keys())


def normalize_conversation_style(raw: Any) -> str:
    s = str(raw or "").strip().lower()
    if s in VALID_STYLES:
        return s
    return DEFAULT_STYLE


def system_addon_for_conversation_style(slug: Any) -> str:
    key = normalize_conversation_style(slug)
    return _PROFILES[key][2]


def profile_title_and_help(slug: Any) -> Tuple[str, str]:
    key = normalize_conversation_style(slug)
    return _PROFILES[key][0], _PROFILES[key][1]


def all_profiles_for_ui() -> List[Tuple[str, str, str]]:
    """Список (slug, короткая метка, описание)."""
    return [(k, v[0], v[1]) for k, v in _PROFILES.items()]


def keyboard_rows() -> List[List[Tuple[str, str]]]:
    """Строки кнопок: (текст, callback_data) callback_data = cstyle:<slug>."""
    rows: List[List[Tuple[str, str]]] = []
    row: List[Tuple[str, str]] = []
    for slug, short, _help in all_profiles_for_ui():
        row.append((short, f"cstyle:{slug}"))
        if len(row) >= 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows
