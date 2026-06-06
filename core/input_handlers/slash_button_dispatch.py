"""Единая точка: inline-кнопка → slash-команда (user + admin отчёты)."""
from __future__ import annotations

from typing import Any, Optional

from aiogram.types import Message

from core.input_handlers.admin_access import effective_user_scope
from core.input_handlers.admin_slash_dispatch import try_dispatch_admin_slash
from core.input_handlers.inline_slash_dispatch import try_dispatch_inline_slash


async def dispatch_slash_from_button(
    layer: Any,
    message: Message,
    text: str,
    *,
    actor_user_id: Optional[str] = None,
) -> bool:
    """
    Возвращает True, если команда выполнена.
    Иначе False — вызывающий может показать подсказку или fallback.
    """
    raw = (text or "").strip()
    if not raw.startswith("/"):
        return False
    msg = message.model_copy(update={"text": raw})
    uid = (actor_user_id or "").strip()

    async def _run() -> bool:
        if await try_dispatch_inline_slash(layer, msg, raw):
            return True
        if await try_dispatch_admin_slash(layer, msg, raw):
            return True
        return False

    if uid:
        with effective_user_scope(uid):
            return await _run()
    return await _run()
