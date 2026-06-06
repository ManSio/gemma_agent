"""Контекст ветки reply в Telegram."""
from unittest.mock import MagicMock

from core.input_layer import telegram_forward_preamble, telegram_reply_context_from_message


def _msg(text: str = "", *, reply_to=None, username: str | None = "alice"):
    m = MagicMock()
    m.text = text or None
    m.caption = None
    m.reply_to_message = reply_to
    m.photo = None
    m.document = None
    m.sticker = None
    fu = MagicMock()
    fu.id = 101
    fu.username = username
    m.from_user = fu
    return m


def test_reply_chain_two_levels() -> None:
    parent = _msg("объясни про X", reply_to=None, username="bot")
    parent.reply_to_message = None
    child = _msg("ок", reply_to=parent, username="bob")
    out = telegram_reply_context_from_message(child, bot_user_id=999)
    assert "уровень 1" in out
    assert "объясни про X" in out
    assert "@bot" in out


def test_forward_preamble_skips_without_forward() -> None:
    m = MagicMock()
    m.forward_origin = None
    assert telegram_forward_preamble(m) == ""
