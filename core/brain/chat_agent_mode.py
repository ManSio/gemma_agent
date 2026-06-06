"""Режим «как браузерный чат» (DeepSeek/ChatGPT): один LLM + память, tools по необходимости."""
from __future__ import annotations

import os
from typing import Any, List, Optional

from core.runtime_telegram_settings import effective_bool


def chat_agent_mode_enabled() -> bool:
    return effective_bool("BRAIN_CHAT_AGENT_MODE", default=False)


def direct_dialog_recent_turns() -> int:
    try:
        v = int((os.getenv("BRAIN_DIRECT_DIALOG_RECENT_TURNS") or "10").strip())
    except ValueError:
        v = 10
    return max(4, min(16, v))


def direct_dialog_min_chars(*, has_recent: bool) -> int:
    if not chat_agent_mode_enabled():
        return 8
    return 2 if has_recent else 4


def direct_dialog_max_chars() -> int:
    if chat_agent_mode_enabled():
        try:
            v = int((os.getenv("BRAIN_CHAT_AGENT_MAX_CHARS") or "8000").strip())
        except ValueError:
            v = 8000
        return max(500, min(v, 12000))
    return 720


def use_premium_for_direct() -> bool:
    """В chat-agent режиме можно взять основную модель вместо free-only."""
    if not chat_agent_mode_enabled():
        return False
    return effective_bool("BRAIN_CHAT_AGENT_USE_MAIN_MODEL", default=True)
