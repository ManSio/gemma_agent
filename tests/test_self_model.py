from __future__ import annotations

import os

from core.self_model import default_self_model, hydrate_self_model_from_kv, update_self_model_after_turn


def test_hydrate_self_model_default_when_missing():
    prev = os.environ.get("SELF_MODEL_ENABLED")
    try:
        os.environ["SELF_MODEL_ENABLED"] = "1"
        rec = hydrate_self_model_from_kv("u1", {})
        assert isinstance(rec.get("self_model"), dict)
        assert rec["self_model"].get("identity", {}).get("platform") == "gemma_bot"
    finally:
        if prev is None:
            os.environ.pop("SELF_MODEL_ENABLED", None)
        else:
            os.environ["SELF_MODEL_ENABLED"] = prev


def test_update_self_model_tracks_constraints():
    base = default_self_model()
    updated = update_self_model_after_turn(
        user_id="",
        base=base,
        outcome="failure",
        intent="reasoning",
        module="reasoning_hub",
        task_tier="deep",
        safe_mode=True,
    )
    constraints = set(updated.get("active_constraints") or [])
    assert "safe_mode" in constraints
    assert "recovery_bias" in constraints
    route = updated.get("last_route") or {}
    assert route.get("intent") == "reasoning"


def test_update_self_model_trend_and_confidence_window():
    prev = os.environ.get("SELF_MODEL_TREND_WINDOW")
    try:
        os.environ["SELF_MODEL_TREND_WINDOW"] = "4"
        sm = default_self_model()
        for outcome in ["ok", "ok", "failure", "failure", "failure"]:
            sm = update_self_model_after_turn(
                user_id="",
                base=sm,
                outcome=outcome,
                intent="general",
                module="chat-orchestrator",
                task_tier="shallow",
                safe_mode=False,
            )
        recent = sm.get("recent_outcomes") or []
        assert len(recent) == 4
        conf = sm.get("confidence_summary") or {}
        assert conf.get("trend") in {"down", "stable", "up"}
        assert isinstance(conf.get("score"), float)
        assert "stability_guard" in set(sm.get("active_constraints") or [])
    finally:
        if prev is None:
            os.environ.pop("SELF_MODEL_TREND_WINDOW", None)
        else:
            os.environ["SELF_MODEL_TREND_WINDOW"] = prev
