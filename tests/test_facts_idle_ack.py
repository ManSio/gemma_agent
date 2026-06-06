"""«да» без pending фактов — не «уже записано»."""
from __future__ import annotations

from core.user_facts import try_facts_shortcut_payload


def test_idle_yes_returns_none():
    assert try_facts_shortcut_payload("да", {"facts": {"name": "Миша"}}) is None
    assert try_facts_shortcut_payload("ok", {}) is None
