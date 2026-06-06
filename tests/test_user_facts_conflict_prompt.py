"""Перезапись фактов: уточнение «было → станет» перед записью."""
from __future__ import annotations

from unittest.mock import MagicMock

from core.user_facts import (
    UserFactsManager,
    build_facts_confirmation_prompt_ru,
)


def _manager_with_facts(facts: dict) -> tuple[UserFactsManager, dict]:
    store = MagicMock()
    rec: dict = {
        "user_facts": dict(facts),
        "user_facts_meta": {},
        "pending_facts_confirmation": {},
        "pending_facts_overwrite": {},
    }

    def _load(uid, gid):
        return dict(rec)

    def _save(uid, gid, r):
        rec.clear()
        rec.update(r)

    store.load.side_effect = _load
    store.save.side_effect = _save
    return UserFactsManager(behavior_store=store), rec


def test_build_conflict_prompt_old_new():
    prompt = build_facts_confirmation_prompt_ru(
        {"pet_cat": "Мурза"},
        {},
        {"pet_cat": {"field": "pet_cat", "value": "Барсик"}},
    )
    assert "Мурза" in prompt
    assert "Барсик" in prompt
    assert "да" in prompt.lower()


def test_process_turn_pet_rename_goes_to_overwrite_pending():
    m, rec = _manager_with_facts({"pet_cat": "Мурза"})
    out = m.process_turn("u1", None, "теперь кошку зовут Барсик")
    assert out.get("conflicting_candidates", {}).get("pet_cat")
    assert rec.get("pending_facts_overwrite", {}).get("pet_cat")
    cp = str(out.get("confirmation_prompt") or "")
    assert "Мурза" in cp and "Барсик" in cp


def test_explicit_remember_new_pet_commits_without_prompt():
    m, rec = _manager_with_facts({})
    out = m.process_turn("u1", None, "запомни кошку Мурка")
    assert out.get("committed_facts_this_turn")
    assert rec.get("user_facts", {}).get("pet_cat") == "Мурка"
    assert not out.get("confirmation_prompt")


def test_explicit_remember_overwrite_still_asks():
    m, rec = _manager_with_facts({"pet_cat": "Мурза"})
    out = m.process_turn("u1", None, "запомни кошку Барсик")
    assert out.get("conflicting_candidates", {}).get("pet_cat")
    cp = str(out.get("confirmation_prompt") or "")
    assert "Мурза" in cp and "Барсик" in cp
