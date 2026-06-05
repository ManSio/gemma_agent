from __future__ import annotations

import re
from typing import Any

from aiogram import F
from aiogram.filters import Command
from aiogram.types import Message

from core.input_handlers.telegram_command_runners import (
    run_chat_style,
    run_corpus_books,
    run_corpus_delete,
    run_corpus_doc,
    run_corpus_docs,
    run_corpus_file,
    run_correct,
    run_facts,
    run_facts_refresh,
    run_facts_reset,
    run_filefrom,
    run_forget,
    run_id,
    run_new_conversation,
    run_me,
    run_psych,
    run_rate,
    run_twin,
)


def register(layer: Any) -> None:
    dp = layer.dp

    @dp.message(Command("id", ignore_mention=True))
    async def handle_id(message: Message):
        await run_id(layer, message)

    @dp.message(Command("me", ignore_mention=True))
    async def handle_me(message: Message):
        await run_me(layer, message)

    @dp.message(Command("psych", ignore_mention=True))
    async def handle_psych(message: Message):
        await run_psych(layer, message)

    @dp.message(Command("twin", ignore_mention=True))
    async def handle_twin(message: Message):
        await run_twin(layer, message)

    @dp.message(Command("chat_style", ignore_mention=True))
    async def handle_chat_style_cmd(message: Message):
        await run_chat_style(layer, message)

    @dp.message(Command("style", ignore_mention=True))
    async def handle_style_alias(message: Message):
        await run_chat_style(layer, message)

    # Кириллица в Command() на части клиентов/версий aiogram не матчится — отдельный фильтр.
    @dp.message(F.text.regexp(r"^/стиль(?:@[A-Za-z0-9_]+)?(?:\s|$)", flags=re.IGNORECASE))
    async def handle_chat_style_ru(message: Message):
        await run_chat_style(layer, message)

    @dp.message(Command("facts", ignore_mention=True))
    async def handle_facts(message: Message):
        await run_facts(layer, message)

    @dp.message(Command("rate", ignore_mention=True))
    async def handle_rate(message: Message):
        await run_rate(layer, message)

    @dp.message(Command("correct", ignore_mention=True))
    async def handle_correct(message: Message):
        await run_correct(layer, message)

    @dp.message(Command("forget", ignore_mention=True))
    async def handle_forget(message: Message):
        await run_forget(layer, message)

    @dp.message(F.text.regexp(r"^/new(?:@[A-Za-z0-9_]+)?(?:\s|$)", flags=re.IGNORECASE))
    async def handle_new_conversation(message: Message):
        await run_new_conversation(layer, message)

    @dp.message(Command("facts_refresh", ignore_mention=True))
    async def handle_facts_refresh(message: Message):
        await run_facts_refresh(layer, message)

    @dp.message(Command("facts_reset", ignore_mention=True))
    async def handle_facts_reset(message: Message):
        await run_facts_reset(layer, message)

    @dp.message(Command("filefrom", ignore_mention=True))
    async def handle_filefrom(message: Message):
        await run_filefrom(layer, message)

    @dp.message(Command("corpus_doc", ignore_mention=True))
    async def handle_corpus_doc(message: Message):
        await run_corpus_doc(layer, message)

    @dp.message(Command("corpus_books", ignore_mention=True))
    async def handle_corpus_books(message: Message):
        await run_corpus_books(layer, message)

    @dp.message(Command("corpus_docs", ignore_mention=True))
    async def handle_corpus_docs(message: Message):
        await run_corpus_docs(layer, message)

    @dp.message(Command("corpus_file", ignore_mention=True))
    async def handle_corpus_file(message: Message):
        await run_corpus_file(layer, message)

    @dp.message(Command("corpus_delete", ignore_mention=True))
    async def handle_corpus_delete(message: Message):
        await run_corpus_delete(layer, message)
