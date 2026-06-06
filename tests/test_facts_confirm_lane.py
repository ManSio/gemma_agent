"""Дорожка подтверждения фактов без полного LLM."""
from __future__ import annotations

from core.user_facts import facts_save_confirm_lane_eligible, try_facts_shortcut_payload


def test_confirm_lane_needs_new_candidates():
    ff = {
        "confirmation_prompt": "Запомнить имя кошки? Ответь «да» или «нет».",
        "new_candidates": {"pet_cat": {"field": "pet_cat", "value": "Мурза"}},
    }
    assert facts_save_confirm_lane_eligible(ff) is True


def test_confirm_lane_false_without_prompt():
    assert facts_save_confirm_lane_eligible({"new_candidates": {"x": 1}}) is False


def test_shortcut_after_commit():
    ff = {
        "facts": {"pet_cat": "Мурза"},
        "pending_confirmation": {},
        "committed_facts_this_turn": True,
    }
    assert "Мурза" in (try_facts_shortcut_payload("да", ff) or "")


def test_idle_yes_without_pending_returns_none():
    ff = {"facts": {"pet_cat": "Мурза"}, "pending_confirmation": {}}
    assert try_facts_shortcut_payload("да", ff) is None


def test_shortcut_not_when_pending():
    ff = {"pending_confirmation": {"pet_cat": {}}, "confirmation_prompt": "Запомнить?"}
    assert try_facts_shortcut_payload("да", ff) is None


def test_shortcut_not_when_assistant_offered_search():
    ff = {"facts": {"name": "Test"}, "pending_confirmation": {}}
    recent = [
        {"role": "user", "text": "да"},
        {
            "role": "assistant",
            "text": "могу попробовать перепроверить поиск именно по этой фамилии",
        },
    ]
    assert try_facts_shortcut_payload("да", ff, recent_dialogue=recent) is None
