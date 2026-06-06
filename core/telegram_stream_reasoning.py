"""
Admin-only: показ CoT/reasoning в Telegram stream (private).

Вкл: TELEGRAM_ADMIN_STREAM_REASONING + toggle aus:asr в /admin → Настройки.
"""
from __future__ import annotations

import contextvars
import os
from typing import Optional

from core.runtime_telegram_settings import effective_bool

_armed: contextvars.ContextVar[bool] = contextvars.ContextVar("telegram_stream_reasoning_armed", default=False)


def admin_stream_reasoning_env_default() -> bool:
    return effective_bool("TELEGRAM_ADMIN_STREAM_REASONING", default=False)


def admin_stream_reasoning_effective(*, is_admin: bool) -> bool:
    if not is_admin:
        return False
    return admin_stream_reasoning_env_default()


def stream_reasoning_armed() -> bool:
    return bool(_armed.get())


def arm_admin_stream_reasoning(active: bool) -> None:
    _armed.set(bool(active))


def disarm_admin_stream_reasoning() -> None:
    _armed.set(False)


def reasoning_display_max_chars() -> int:
    try:
        v = int((os.getenv("TELEGRAM_STREAM_REASONING_MAX_CHARS") or "1800").strip())
    except ValueError:
        v = 1800
    return max(200, min(v, 3500))


def compose_stream_display(*, reasoning: str, content: str) -> str:
    """Сборка текста progress-сообщения: CoT + разделитель + ответ."""
    parts: list[str] = []
    r = (reasoning or "").strip()
    if r:
        cap = reasoning_display_max_chars()
        if len(r) > cap:
            r = r[:cap].rstrip() + "…"
        parts.append(f"🧠 {r}")
    c = (content or "").strip()
    if c:
        if parts:
            parts.extend(["", "—", ""])
        parts.append(c)
    if not parts:
        return "…"
    body = "\n".join(parts)
    return body[:4080]
