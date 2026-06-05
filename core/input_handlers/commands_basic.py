from __future__ import annotations

from typing import Any

from aiogram.filters import Command
from aiogram.types import Message

from core.input_handlers.telegram_command_runners import (
    run_geo_help,
    run_get_mem0_facts,
    run_help,
    run_plugins,
    run_plugins_help,
    run_start,
    run_system_state,
)


def register(layer: Any) -> None:
    dp = layer.dp

    @dp.message(Command("start", ignore_mention=True))
    async def handle_start(message: Message):
        await run_start(layer, message)

    @dp.message(Command("help", ignore_mention=True))
    async def handle_help(message: Message):
        await run_help(layer, message)

    @dp.message(Command("geo_help", ignore_mention=True))
    async def handle_geo_help(message: Message):
        await run_geo_help(layer, message)

    @dp.message(Command("plugins", ignore_mention=True))
    async def handle_plugins(message: Message):
        await run_plugins(layer, message)

    @dp.message(Command("plugins_help", ignore_mention=True))
    async def handle_plugins_help(message: Message):
        await run_plugins_help(layer, message)

    @dp.message(Command("system_state", ignore_mention=True))
    async def handle_system_state(message: Message):
        await run_system_state(layer, message)

    @dp.message(Command("status", ignore_mention=True))
    async def handle_status(message: Message):
        await run_system_state(layer, message)

    @dp.message(Command("get_mem0_facts", ignore_mention=True))
    async def handle_mem0(message: Message):
        await run_get_mem0_facts(layer, message)
