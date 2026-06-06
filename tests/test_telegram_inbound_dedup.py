"""Дедуп входящих Telegram message_id / callback_id."""
from __future__ import annotations

from core.telegram_inbound_dedup import (
    reset_for_tests,
    should_skip_duplicate_callback,
    should_skip_duplicate_message,
)


def setup_function():
    reset_for_tests()


def test_duplicate_message_skipped():
    assert should_skip_duplicate_message("123", 18471) is False
    assert should_skip_duplicate_message("123", 18471) is True


def test_duplicate_callback_skipped():
    assert should_skip_duplicate_callback("cb-1") is False
    assert should_skip_duplicate_callback("cb-1") is True


def test_different_message_ids_not_deduped():
    assert should_skip_duplicate_message("123", 1) is False
    assert should_skip_duplicate_message("123", 2) is False
