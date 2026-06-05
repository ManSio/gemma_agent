"""Разбор поля dialog_turns в agent_test_runner (корпус H3)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import agent_test_runner as atr  # noqa: E402


def test_dialog_turn_messages_prefers_list() -> None:
    assert atr._dialog_turn_messages({"dialog_turns": [" a ", "b"]}) == ["a", "b"]


def test_dialog_turn_messages_fallback_text() -> None:
    assert atr._dialog_turn_messages({"text": "  hello  "}) == ["hello"]


def test_dialog_turn_messages_empty_list_falls_back_to_text() -> None:
    assert atr._dialog_turn_messages({"dialog_turns": [], "text": "x"}) == ["x"]


def test_dialog_turn_messages_whitespace_only_falls_back() -> None:
    assert atr._dialog_turn_messages({"dialog_turns": ["", "  "], "text": "ok"}) == ["ok"]
