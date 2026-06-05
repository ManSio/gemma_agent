from __future__ import annotations

from core.behavior_store import BehaviorStore
from core.user_facts import UserFactsManager
from core.user_management_module import UserManagementModule


def test_load_user_profile_aggregate_merges_group_and_dm(tmp_path):
    base = str(tmp_path / "data")
    bs = BehaviorStore(base_dir=base)

    dm = bs.load("42", None)
    dm["user_facts"] = {"name": "Alice", "city": "Berlin"}
    dm["user_facts_meta"] = {
        "name": {"updated_at": "2025-01-01T00:00:00+00:00"},
        "city": {"updated_at": "2025-01-01T00:00:00+00:00"},
    }
    bs.save("42", None, dm)

    g = bs.load("42", "-1001")
    g["user_facts"] = {"city": "Paris", "country": "France"}
    g["user_facts_meta"] = {
        "city": {"updated_at": "2026-01-01T00:00:00+00:00"},
        "country": {"updated_at": "2026-01-01T00:00:00+00:00"},
    }
    g["group_context"] = {"interaction_count": 5}
    g["persona_style_anchor"] = {"tone": "short"}
    bs.save("42", "-1001", g)

    agg = bs.load_user_profile_aggregate("42")
    assert agg["user_facts"]["name"] == "Alice"
    assert agg["user_facts"]["city"] == "Paris"
    assert agg["user_facts"]["country"] == "France"
    assert agg["persona_style_anchor"].get("tone") == "short"


def test_process_turn_saves_after_expired_cleanup(tmp_path):
    base = str(tmp_path / "data")
    bs = BehaviorStore(base_dir=base)
    um = UserFactsManager(behavior_store=bs)
    rec = bs.load("7", None)
    rec["user_facts"] = {"age": "30"}
    rec["user_facts_meta"] = {"age": {"expires_at": "2000-01-01T00:00:00+00:00"}}
    bs.save("7", None, rec)

    um.process_turn("7", None, "")
    disk = bs.load("7", None)
    assert "age" not in (disk.get("user_facts") or {})


def test_facts_refresh_private_runs_all_sessions(tmp_path):
    base = str(tmp_path / "data")
    bs = BehaviorStore(base_dir=base)
    um = UserFactsManager(behavior_store=bs)
    mgmt = UserManagementModule(bs, um)

    dm = bs.load("99", None)
    dm["user_facts"] = {"name": "X"}
    dm["user_facts_meta"] = {"name": {"expires_at": "2000-01-01T00:00:00+00:00"}}
    bs.save("99", None, dm)

    g = bs.load("99", "-9")
    g["user_facts"] = {"city": "Y"}
    g["user_facts_meta"] = {"city": {"expires_at": "2000-01-01T00:00:00+00:00"}}
    bs.save("99", "-9", g)

    mgmt.facts_refresh("99", None)
    assert bs.load("99", None).get("user_facts") == {}
    assert bs.load("99", "-9").get("user_facts") == {}
