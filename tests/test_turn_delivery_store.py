"""Tests for defer store until delivery."""
from __future__ import annotations

import tempfile
import unittest

from core.behavior_store import BehaviorStore
from core.models import Output
from core.turn_delivery_store import (
    attach_pending_to_outputs,
    build_pending_turn_store,
    generation_stale_for_chat,
    patch_plan_meta_shortcut_from_step,
    persist_turn_after_delivery,
)


class TestTurnDeliveryStore(unittest.TestCase):
    def test_stale_generation_blocks_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = BehaviorStore(base_dir=tmp)
            uid = "u1"
            store.bump_turn_generation(uid, None)
            store.bump_turn_generation(uid, None)
            pending = build_pending_turn_store(
                user_payload="hi",
                draft_assistant="draft",
            )
            ok = persist_turn_after_delivery(
                behavior_store=store,
                goal_engine=None,
                user_id=uid,
                group_id=None,
                assistant_text="sent",
                pending=pending,
                generation=1,
            )
            self.assertFalse(ok)
            rec = store.load(uid, None)
            self.assertFalse(rec.get("recent_messages"))

    def test_finalize_writes_after_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = BehaviorStore(base_dir=tmp)
            uid = "u2"
            gen = store.bump_turn_generation(uid, None)
            pending = build_pending_turn_store(
                user_payload="привет",
                draft_assistant="draft wrong",
            )
            outputs = [Output(type="text", payload="draft wrong", meta={})]
            attach_pending_to_outputs(outputs, pending)
            ok = persist_turn_after_delivery(
                behavior_store=store,
                goal_engine=None,
                user_id=uid,
                group_id=None,
                assistant_text="финальный ответ",
                pending=pending,
                generation=gen,
            )
            self.assertTrue(ok)
            rec = store.load(uid, None)
            self.assertEqual(rec["recent_messages"][-1]["text"], "финальный ответ")

    def test_patch_plan_shortcut(self) -> None:
        from core.models import Input, Plan, PlanStep

        inp = Input(type="text", payload="погода", meta={"turn_contract": {}})
        plan = Plan(
            steps=[
                PlanStep(
                    module_name="__fallback__",
                    args={
                        "input": inp.model_dump(),
                        "fallback_variant": "weather_direct",
                        "direct_reply": "wx",
                    },
                )
            ],
            mode="full",
        )
        patch_plan_meta_shortcut_from_step(plan)
        _inp = plan.steps[0].args["input"]
        tc = _inp.get("meta", {}).get("turn_contract")
        self.assertEqual(tc.get("short_circuit"), "weather_direct")


if __name__ == "__main__":
    unittest.main()
