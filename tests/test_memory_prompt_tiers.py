"""Память в промпте: user_facts не теряются на профиле short (ULTRA_SHORT)."""
from __future__ import annotations

from core.brain.prompt_modules import build_dynamic_tail


def test_short_profile_includes_pet_cat_without_name():
    parts = {
        "user_facts": {"pet_cat": "Мурза"},
        "recent_messages": [{"role": "user", "content": "как зовут кошку?"}],
    }
    tail = build_dynamic_tail(parts, profile="short", intent="general", ctx={})
    assert "pet_cat" in tail
    assert "Мурза" in tail


def test_short_profile_skips_empty_user_facts():
    parts = {"user_facts": {}, "recent_messages": []}
    tail = build_dynamic_tail(parts, profile="short", intent="general", ctx={})
    assert "user_facts" not in tail


def test_standard_profile_includes_user_facts():
    parts = {"user_facts": {"name": "Миша", "city": "Минск"}}
    tail = build_dynamic_tail(parts, profile="standard", intent="general", ctx={})
    assert "Миша" in tail
    assert "Минск" in tail
