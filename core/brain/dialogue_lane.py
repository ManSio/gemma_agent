"""Выбор узкой дорожки LLM (direct dialog) без полного brain+tools."""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

from core.runtime_telegram_settings import effective_bool

_DIRECT_PROFILES = frozenset(
    {
        "standard",
        "quick_explain",
        "summarization",
        "teacher",
    }
)

_TOOL_CUE_RE = re.compile(
    r"(?i)(?:"
    r"https?://|www\.|"
    r"\b(?:найди|поиск|загугли|search|urlfetch|wikipedia)\b|"
    r"\b(?:погод|weather|курс\s+валют|конверт)\b|"
    r"\b(?:указ|приказ|закон\s+беларус|pravo)\b|"
    r"\b(?:напомни|remind|/radd)\b|"
    r"\b(?:сгенерируй\s+картин|нарисуй|image)\b"
    r")"
)


def direct_dialog_enabled() -> bool:
    return effective_bool("BRAIN_DIRECT_DIALOG_ENABLED", default=False)


def resolve_lane_label(
    *,
    brain_profile: str = "",
    translation_turn: bool = False,
    direct_dialog_used: bool = False,
    fast_chitchat: bool = False,
    tools_used: bool = False,
    deterministic_module: bool = False,
) -> str:
    """
    Метка дорожки для turns.jsonl / диагностики (PRODUCT_FINISH фаза 3).
    deterministic | narrow_llm | direct_llm | tool_llm
    """
    if deterministic_module:
        return "deterministic"
    if translation_turn or fast_chitchat:
        return "narrow_llm"
    if direct_dialog_used:
        return "direct_llm"
    if tools_used:
        return "tool_llm"
    prof = (brain_profile or "").strip().lower()
    if prof in ("fast_chitchat", "translation_reply", "math_linear"):
        return "narrow_llm"
    return "tool_llm" if prof not in _DIRECT_PROFILES else "direct_llm"


def is_direct_dialog_eligible(
    user_text: str,
    *,
    brain_profile: str = "",
    task_facts: Optional[Dict[str, Any]] = None,
    translation_turn: bool = False,
    task_tier: str = "",
    tools_mode: str = "",
    has_document_intake: bool = False,
    has_file_context: bool = False,
    recent_dialogue: Optional[List[Any]] = None,
) -> bool:
    if not direct_dialog_enabled():
        return False
    if translation_turn:
        return False
    t = (user_text or "").strip()
    if not t or t.startswith("/"):
        return False
    _chat_agent = False
    try:
        from core.brain.chat_agent_mode import (
            chat_agent_mode_enabled,
            direct_dialog_max_chars,
            direct_dialog_min_chars,
        )

        _chat_agent = chat_agent_mode_enabled()
        _has_recent = bool(recent_dialogue)
        _min = direct_dialog_min_chars(has_recent=_has_recent)
        _max = direct_dialog_max_chars()
    except Exception:
        _min, _max = 8, 720
    if len(t) < _min or len(t) > _max:
        return False
    if not _chat_agent and t.count("?") >= 2:
        return False
    prof = (brain_profile or "standard").strip().lower()
    if prof not in _DIRECT_PROFILES:
        return False
    if (task_tier or "").strip().lower() in ("deep", "nested"):
        return False
    if (tools_mode or "").strip().lower() in ("full", "all"):
        return False
    if has_document_intake or has_file_context:
        return False
    if _TOOL_CUE_RE.search(t):
        return False
    tf = task_facts if isinstance(task_facts, dict) else {}
    if any(tf.get(k) for k in ("is_weather", "is_currency", "is_time", "is_news", "is_pasted_article")):
        return False
    return True
