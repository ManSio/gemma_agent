"""
Slash-команды, которые обрабатываются только через aiogram Command-* (не через оркестратор).

Иначе одно сообщение обрабатывается дважды: хендлер + chat-orchestrator
(дубли, «рассуждения» про /admin_*).

Список читается из core.command_catalog.CORE_COMMANDS.
"""
from __future__ import annotations

from typing import FrozenSet

from core.command_catalog import (
    get_core_exclusive_tokens,
    is_admin_command_pattern,
    normalize_command_token,
)


def _exclusive_tokens() -> FrozenSet[str]:
    return get_core_exclusive_tokens()


# Снимок токенов для обратной совместимости (tests, type checks).
_EXCLUSIVE_SLASH: FrozenSet[str] = _exclusive_tokens()


def slash_command_token(text: str) -> str:
    return normalize_command_token(text)


def orchestrator_should_skip_slash(text: str) -> bool:
    tok = slash_command_token(text)
    if not tok:
        return False
    if is_admin_command_pattern(tok):
        return True
    return tok in _exclusive_tokens()
