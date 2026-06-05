"""Вставка статьи — не спрашивать страну и не вешать confirm lane."""
from __future__ import annotations

from unittest.mock import MagicMock

from core.user_facts import UserFactsManager, facts_save_confirm_lane_eligible

PASTE = (
    "Тихановская выступила с обращением к гражданам. "
    "Она заявила о необходимости перемен в стране. " * 12
    + "\n\nЧитайте также на myfin.by подробнее о событиях."
)


def test_extract_no_country_from_paste_bonus():
    mgr = UserFactsManager(behavior_store=MagicMock(), digital_twin=None, mem0_memory=None)
    out = mgr.extract_facts(PASTE)
    fields = {x.get("field") for x in out}
    assert "country" not in fields


def test_process_turn_suppresses_confirmation(monkeypatch):
    store = MagicMock()
    store.load.return_value = {
        "user_facts": {},
        "user_facts_meta": {},
        "pending_facts_confirmation": {},
        "pending_facts_overwrite": {},
    }
    mgr = UserFactsManager(behavior_store=store, digital_twin=None, mem0_memory=None)
    flow = mgr.process_turn("u1", None, PASTE)
    assert flow.get("suppress_confirmation") is True
    assert not flow.get("confirmation_prompt")
    assert facts_save_confirm_lane_eligible(flow) is False
