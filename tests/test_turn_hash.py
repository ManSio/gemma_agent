"""Tests for plan vs brain turn hash drift."""
from __future__ import annotations

import unittest

from core.turn_hash import (
    brain_turn_hash,
    check_and_record_drift,
    plan_turn_hash,
    plan_turn_hash_from_meta,
)


class TestTurnHash(unittest.TestCase):
    def test_same_inputs_same_hash(self) -> None:
        a = plan_turn_hash(profile="standard", lane="DIALOGUE", referent="thread")
        b = plan_turn_hash(profile="standard", lane="DIALOGUE", referent="thread")
        self.assertEqual(a, b)

    def test_drift_detected(self) -> None:
        ph = plan_turn_hash(profile="standard", lane="DIALOGUE")
        bh = brain_turn_hash(profile="quick_explain", lane="DIALOGUE")
        self.assertTrue(check_and_record_drift(plan_hash=ph, brain_hash=bh, trace_id="t1"))

    def test_match_no_drift(self) -> None:
        h = plan_turn_hash(profile="standard", lane="DIALOGUE")
        self.assertFalse(check_and_record_drift(plan_hash=h, brain_hash=h, trace_id="t2"))

    def test_plan_hash_from_meta(self) -> None:
        from core.models import Input, Plan, PlanStep

        inp = Input(
            type="text",
            payload="hi",
            meta={
                "turn_contract": {"lane": "FACT", "short_circuit": "weather_direct"},
                "plan_turn_meaning": {"referent": "world"},
            },
        )
        plan = Plan(
            steps=[PlanStep(module_name="__fallback__", args={"fallback_variant": "weather_direct", "input": inp.model_dump()})],
            mode="full",
        )
        h = plan_turn_hash_from_meta(inp.meta, plan)
        self.assertEqual(len(h), 16)


if __name__ == "__main__":
    unittest.main()
