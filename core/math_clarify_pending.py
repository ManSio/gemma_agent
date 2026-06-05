"""Последнее сообщение, на котором сработал уточняющий fallback math_ambiguous (для кнопки «без калькулятора»)."""

from __future__ import annotations

from typing import Dict, Optional

_PENDING: Dict[str, str] = {}


def _key(user_id: str, chat_id: str) -> str:
    return f"{user_id}:{chat_id}"


def set_pending(user_id: str, chat_id: str, text: str) -> None:
    t = (text or "").strip()
    if not t or not user_id or not chat_id:
        return
    _PENDING[_key(user_id, chat_id)] = t[:12000]


def pop_pending(user_id: str, chat_id: str) -> Optional[str]:
    return _PENDING.pop(_key(user_id, chat_id), None)
