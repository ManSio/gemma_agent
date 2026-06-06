"""
Маршрутизация slash-команд, обрабатываемых только через aiogram Command(...),
когда эффективный текст берётся из payload (inline-кнопки, caption и т.д.).

Источник истины — core.command_catalog.CORE_COMMANDS. Локальной таблицы
команд здесь больше нет: чтобы добавить runner, заведи запись в каталоге.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram.types import Message

from core.command_catalog import get_core_runner_attrs, normalize_command_token
from core.input_handlers import telegram_command_runners as _runners

Runner = Callable[[Any, Message], Awaitable[None]]


def _runner_attrs() -> Dict[str, str]:
    """Карта токен → имя runner-функции (читаем каталог при каждом вызове,
    чтобы тесты могли monkeypatch'ить CORE_COMMANDS)."""
    return get_core_runner_attrs()


# Экспортируем «снимок» для обратной совместимости с тестами и кодом,
# который читал INLINE_SLASH_RUNNER_ATTRS как обычный dict.
INLINE_SLASH_RUNNER_ATTRS: Dict[str, str] = _runner_attrs()


async def dispatch_core_slash_runner(layer: Any, message: Message, text: str) -> bool:
    """Вызвать runner из CORE_COMMANDS (без дубля оркестратора)."""
    raw = (text or "").strip()
    token = normalize_command_token(raw)
    if not token:
        return False
    attr = _runner_attrs().get(token)
    if not attr:
        return False
    fn = getattr(_runners, attr, None)
    if not callable(fn):
        return False
    msg = message.model_copy(update={"text": raw})
    await fn(layer, msg)
    return True


async def try_dispatch_inline_slash(layer: Any, message: Message, text: str) -> bool:
    return await dispatch_core_slash_runner(layer, message, text)
