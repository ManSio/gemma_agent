"""
Обратная связь по входящим сообщениям: видно, что бот «оживил» диалог.

- Сообщения, начинающиеся с /: индикатор «печатает» (+ опционально короткий текст).
- Логируем первый токен команды для отладки.

Переменные окружения:
  TELEGRAM_SLASH_TYPING — по умолчанию true: send_chat_action(typing) для /…
  TELEGRAM_COMMAND_ACK — если true: короткое «Команда принята…» вторым сообщением
"""
from __future__ import annotations

import logging
import os
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from core.telegram_util import sanitize_html

logger = logging.getLogger(__name__)


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class SlashCommandFeedbackMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            text = (event.text or "").strip()
            if text.startswith("/"):
                first = text.split(None, 1)[0]
                logger.info("slash_input: %s chat_id=%s user=%s", first, event.chat.id, event.from_user.id if event.from_user else None)
                if _truthy("TELEGRAM_SLASH_TYPING", True):
                    try:
                        await event.bot.send_chat_action(event.chat.id, "typing")
                    except Exception as e:
                        logger.debug("send_chat_action typing: %s", e)
                if _truthy("TELEGRAM_COMMAND_ACK", False):
                    try:
                        await event.answer(
                            sanitize_html(f"✓ <b>Команда принята:</b> <code>{first}</code>\n<i>Выполняю…</i>"),
                            parse_mode="HTML",
                        )
                    except Exception as e:
                        logger.debug("command ack message: %s", e)
        return await handler(event, data)
