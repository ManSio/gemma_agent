"""Регрессия: имена питомцев в user_facts."""
from __future__ import annotations

from unittest.mock import MagicMock

from core.user_facts import UserFactsManager, format_fact_fields_nice_ru


def test_extract_pet_cat_zovut():
    m = UserFactsManager(behavior_store=MagicMock())
    out = m.extract_facts("Запомни: мою кошку зовут Мурза")
    pets = [x for x in out if x.get("field") == "pet_cat" and x.get("valid")]
    assert pets
    assert pets[0].get("value") == "Мурза"


def test_extract_pet_cat_short_form():
    m = UserFactsManager(behavior_store=MagicMock())
    out = m.extract_facts("запомни кошка Мурка")
    pets = [x for x in out if x.get("field") == "pet_cat" and x.get("valid")]
    assert pets
    assert "Мурка" in (pets[0].get("value") or "")


def test_format_pet_label_ru():
    assert "кошки" in format_fact_fields_nice_ru({"pet_cat"})


def test_commit_pet_cat_persists():
    store = MagicMock()
    rec: dict = {"user_facts": {}, "user_facts_meta": {}}

    def _load(uid, gid):
        return dict(rec)

    def _save(uid, gid, r):
        rec.clear()
        rec.update(r)

    store.load.side_effect = _load
    store.save.side_effect = _save
    m = UserFactsManager(behavior_store=store)
    m.commit_validated(
        "u1",
        None,
        {
            "pet_cat": {
                "field": "pet_cat",
                "value": "Мурза",
                "confidence": 0.95,
                "valid": True,
                "source": "test",
            }
        },
    )
    assert rec.get("user_facts", {}).get("pet_cat") == "Мурза"
