"""Согласованность private_intro с slash-командами вне pipeline."""
from unittest.mock import MagicMock

from core.input_handlers.telegram_command_runners import _note_private_seen_after_slash_command
from core.input_layer import InputLayer


def test_should_not_intro_on_substantive_first_message() -> None:
    layer = MagicMock(spec=InputLayer)
    layer._private_intro_enabled = InputLayer._private_intro_enabled.__get__(layer, InputLayer)
    layer._seen_private_users = set()
    layer._user_has_prior_private_dialogue = lambda _uid: False  # type: ignore[method-assign]
    layer._persist_seen_private_intro_user = lambda _uid: None  # type: ignore[method-assign]
    assert InputLayer._should_send_private_intro(layer, "42", "Какие новости") is False


def test_should_intro_on_short_greeting() -> None:
    layer = MagicMock(spec=InputLayer)
    layer._private_intro_enabled = InputLayer._private_intro_enabled.__get__(layer, InputLayer)
    layer._seen_private_users = set()
    layer._user_has_prior_private_dialogue = lambda _uid: False  # type: ignore[method-assign]
    layer._persist_seen_private_intro_user = lambda _uid: None  # type: ignore[method-assign]
    assert InputLayer._should_send_private_intro(layer, "42", "привет") is True


def test_note_private_seen_calls_layer_for_private_dm() -> None:
    layer = MagicMock()
    msg = MagicMock()
    msg.chat.type = "private"
    msg.from_user.id = 42
    _note_private_seen_after_slash_command(layer, msg)
    layer.note_private_user_seen_for_intro.assert_called_once_with("42")


def test_note_private_seen_skips_groups() -> None:
    layer = MagicMock()
    msg = MagicMock()
    msg.chat.type = "supergroup"
    msg.from_user.id = 1
    _note_private_seen_after_slash_command(layer, msg)
    layer.note_private_user_seen_for_intro.assert_not_called()
