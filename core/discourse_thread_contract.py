"""Публичный контракт нити: followup, content-tokens (без private imports из discourse_resolver)."""
from __future__ import annotations

from typing import Any, Dict, Optional, Set


def immediate_thread_followup(user_text: str, context: Optional[Dict[str, Any]] = None) -> bool:
    """Короткий ход сразу после ответа — stay, если нет новых content-токенов вне нити."""
    from core.brain.discourse_resolver import _immediate_thread_followup

    return _immediate_thread_followup(user_text, context)


def thread_content_tokens(text: str, *, min_len: int = 4) -> Set[str]:
    """Content-токены реплики для overlap нити."""
    from core.brain.discourse_resolver import _thread_content_tokens

    return _thread_content_tokens(text, min_len=min_len)
