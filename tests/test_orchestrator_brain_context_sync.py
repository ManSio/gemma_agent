"""Регрессия: телеметрия brain в plan.context после execute (C6)."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.models import Output, Plan, PlanStep
from core.orchestrator import Orchestrator, _sync_brain_context_to_plan_step


class _TelemetryEchoModule:
    name = "chat-orchestrator"

    async def execute(self, args):
        ctx = args.get("context") or {}
        ctx["brain_turn_telemetry"] = {
            "prompt_tokens_est": 1500,
            "brain_recent_limit": 12,
            "brain_profile": "standard",
        }
        ds = ctx.setdefault("dialogue_state", {})
        if isinstance(ds, dict):
            ds.update(ctx["brain_turn_telemetry"])
        return [Output(type="text", payload="ok", meta={"module": self.name})]


class TestOrchestratorBrainContextSync(unittest.IsolatedAsyncioTestCase):
    def test_sync_merges_telemetry_into_plan_context(self) -> None:
        plan_ctx: dict = {"user_id": "u1", "dialogue_state": {}}
        exec_ctx: dict = {
            "user_id": "u1",
            "brain_turn_telemetry": {
                "prompt_tokens_est": 900,
                "brain_recent_limit": 12,
                "brain_profile": "standard",
            },
            "dialogue_state": {"prompt_tokens_est": 900, "brain_recent_limit": 12},
        }
        step = PlanStep(
            module_name="chat-orchestrator",
            args={"context": plan_ctx, "input": {"payload": "hi"}},
        )
        _sync_brain_context_to_plan_step(step, exec_ctx)
        self.assertEqual(plan_ctx["brain_turn_telemetry"]["brain_recent_limit"], 12)
        self.assertEqual(plan_ctx["dialogue_state"]["prompt_tokens_est"], 900)

    async def test_execute_step_writes_telemetry_to_plan_context(self) -> None:
        orch = Orchestrator.__new__(Orchestrator)
        orch.plugin_registry = MagicMock()
        orch.plugin_controller = MagicMock()
        orch.plugin_controller.is_routable.return_value = True
        mod = MagicMock()
        mod.state.status = "healthy"
        mod.instance = _TelemetryEchoModule()
        orch.plugin_registry.get_module.return_value = mod
        orch.mem0_memory = None

        plan_ctx: dict = {"user_id": "u1", "dialogue_state": {}}
        step = PlanStep(
            module_name="chat-orchestrator",
            args={"context": plan_ctx, "input": {"payload": "тест"}},
        )
        with patch.object(orch, "mem0_memory", None):
            await orch._execute_step(step, user_id="u1", group_id=None)

        self.assertEqual(plan_ctx.get("brain_turn_telemetry", {}).get("brain_recent_limit"), 12)


if __name__ == "__main__":
    unittest.main()
