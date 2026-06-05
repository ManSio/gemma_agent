"""turn_telemetry: decision_trace и stage_ms snapshot."""
from __future__ import annotations

import unittest

from core.observability import OBS
from core.turn_telemetry import build_decision_trace, stage_ms_for_trace_id


class TestTurnTelemetry(unittest.TestCase):
    def test_build_decision_trace_pre_llm(self) -> None:
        dt = build_decision_trace(
            planner_bypass="wall_clock_direct",
            planner_reason="",
            router_route_audit={},
            profile="standard",
            module="__fallback__",
            last_tool="",
            fallback_used=True,
        )
        self.assertEqual(dt.get("pre_llm_variant"), "wall_clock_direct")
        self.assertTrue(dt.get("fallback_used"))

    def test_stage_ms_from_obs_marks(self) -> None:
        ctx = OBS.new_trace()
        OBS.mark(ctx.trace_id, "plan_start")
        OBS.mark(ctx.trace_id, "exec_start")
        snap = stage_ms_for_trace_id(ctx.trace_id)
        self.assertIsNotNone(snap)
        assert snap is not None
        self.assertIn("total", snap)
        self.assertGreaterEqual(snap["total"], 0)
        self.assertIn("plan_start", snap)
        OBS.finish(ctx.trace_id, label="test")


if __name__ == "__main__":
    unittest.main()
