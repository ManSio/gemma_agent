"""Telegram stream reply helpers."""

import asyncio

import pytest

from core.telegram_stream_reply import (
    STOP_PREFIX,
    TelegramStreamEditor,
    _stream_deliver_text,
    build_stop_keyboard,
    request_chat_cancel,
    register_chat_cancel,
    telegram_stream_direct_only,
    telegram_stream_reply_enabled,
    telegram_stream_should_bind,
)


def test_stop_callback_prefix():
    kb = build_stop_keyboard(12345)
    assert kb.inline_keyboard[0][0].callback_data == f"{STOP_PREFIX}12345"


def test_cancel_event():
    async def _run() -> None:
        ev = await register_chat_cancel("chat1")
        assert not ev.is_set()
        assert await request_chat_cancel("chat1")
        assert ev.is_set()

    asyncio.run(_run())


def test_stream_disabled_by_default(monkeypatch):
    monkeypatch.delenv("TELEGRAM_STREAM_REPLY_ENABLED", raising=False)
    assert telegram_stream_reply_enabled() is False
    assert telegram_stream_should_bind(user_text="привет как дела", is_group=False) is False


def test_stream_direct_only_default(monkeypatch):
    assert telegram_stream_direct_only() is True


def test_stream_should_bind_requires_enable(monkeypatch):
    monkeypatch.setenv("TELEGRAM_STREAM_REPLY_ENABLED", "true")
    monkeypatch.setenv("BRAIN_DIRECT_DIALOG_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_STREAM_DIRECT_ONLY", "true")
    assert telegram_stream_should_bind(
        user_text="Объясни кратко зачем небо голубое с точки зрения физики",
        is_group=False,
    )
    assert not telegram_stream_should_bind(
        user_text="https://example.com/article " + ("x" * 900),
        is_group=False,
    )


def test_stream_deliver_skips_placeholder_only():
    class _Bot:
        pass

    ed = TelegramStreamEditor(_Bot(), 1, 2, show_reasoning=False)
    assert _stream_deliver_text(body="", editor=ed, show_reasoning=False) == ""
    assert _stream_deliver_text(body="…", editor=ed, show_reasoning=False) == ""
    assert _stream_deliver_text(body="Привет", editor=ed, show_reasoning=False) == "Привет"
