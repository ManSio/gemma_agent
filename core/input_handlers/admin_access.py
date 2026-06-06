from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Optional

from aiogram.types import Message

from core.telegram_util import sanitize_html

_ADMIN_DENY = (
    "⛔ <b>Нет доступа</b>\n"
    "Команда только для администраторов. Укажите свой Telegram ID в <code>ADMIN_USER_IDS</code> "
    "или <code>ADMIN_NOTIFY_USER_IDS</code> в .env (оба списка дают права на /admin)."
)

# Callback-кнопки (/help → Латки и т.д.) шлют synthetic_payload на message бота: from_user = бот.
# В пайплайне уже есть user_id нажавшего (actor_user_id) — кладём сюда на время обработки.
_effective_telegram_user_id: ContextVar[Optional[str]] = ContextVar("effective_telegram_user_id", default=None)


@contextmanager
def effective_user_scope(user_id: str) -> Iterator[None]:
    uid = (user_id or "").strip() or None
    token = _effective_telegram_user_id.set(uid)
    try:
        yield
    finally:
        _effective_telegram_user_id.reset(token)


def effective_admin_user_id(message: Message, args: str = "") -> str:
    """
    Telegram user_id для admin-отчётов (reputation, digest, session_task).
    При callback на сообщении бота from_user — бот; приоритет: args → actor_user_id → from_user.
    """
    raw = (args or "").strip()
    if raw:
        return raw.split()[0]
    ctx_uid = (_effective_telegram_user_id.get() or "").strip()
    if ctx_uid:
        return ctx_uid
    if message.from_user is not None:
        return str(message.from_user.id)
    return ""


async def admin_guard(message: Message, layer: Any) -> bool:
    ctx_uid = (_effective_telegram_user_id.get() or "").strip()
    if ctx_uid:
        check_id = ctx_uid
    elif message.from_user is not None:
        check_id = str(message.from_user.id)
    else:
        check_id = ""
    if check_id and layer._admin_module.is_admin(check_id):
        return True
    await message.answer(sanitize_html(_ADMIN_DENY), parse_mode="HTML")
    return False
