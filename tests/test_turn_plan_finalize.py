"""Tests for turn plan finalize (Phase 0.3 close)."""
from __future__ import annotations

import unittest

from core.models import Input, Plan, PlanStep
from core.turn_plan_finalize import finalize_direct_plan


class TestTurnPlanFinalize(unittest.TestCase):
    def test_finalize_stamps_meaning_and_hash(self) -> None:
        inp = Input(type="text", payload="погода", meta={"trace_id": "t1", "turn_generation": 3})
        plan = Plan(
            steps=[
                PlanStep(
                    module_name="__fallback__",
                    args={
                        "input": inp.model_dump(),
                        "fallback_variant": "weather_direct",
                        "direct_reply": "ясно",
                    },
                )
            ],
            mode="full",
        )
        meta = dict(inp.meta or {})
        meta["user_id"] = "u1"
        out = finalize_direct_plan(plan, meta, user_text="погода в минске", persisted={})
        step_inp = out.steps[0].args["input"]
        step_meta = step_inp.get("meta") if isinstance(step_inp, dict) else {}
        self.assertIsInstance(step_meta.get("plan_turn_meaning"), dict)
        self.assertTrue(str(step_meta.get("plan_turn_hash") or ""))
        tc = step_meta.get("turn_contract")
        self.assertIsInstance(tc, dict)
        self.assertEqual(str(tc.get("short_circuit") or ""), "weather_direct")


if __name__ == "__main__":
    unittest.main()
