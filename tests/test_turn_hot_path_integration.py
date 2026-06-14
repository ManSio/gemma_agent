"""Hot-path integration: Orchestrator plan → execute_plan → defer → finalize."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.behavior_store import BehaviorStore
from core.models import Input
from core.orchestrator import Orchestrator
from core.plugin_registry import PluginRegistry
from core.policy_engine import PolicyEngine
from core.turn_delivery_store import finalize_delivery_from_output_meta
from core.turn_regression import load_regression_cases

_TURN_ENV = {
    "TURN_CONTRACT_ENABLED": "true",
    "TURN_DEFER_STORE_ENABLED": "true",
    "ANTI_ECHO_GUARD_ENABLED": "true",
    "TURN_STICKY_LANE_ENABLED": "true",
    "TURN_CORRECTION_OVERRIDE_ENABLED": "true",
    "TURN_HASH_DRIFT_ENABLED": "true",
    "TURN_PROMPT_ADDITIVE_ENABLED": "true",
    "TURN_FINGERPRINT_ALERT_ENABLED": "true",
    "GOAL_RUNNER_ENABLED": "false",
}

_DIRECT_MOCKS = {
    "weather_direct": ("core.weather_reply.try_weather_reply_sync", "В Минске +15°C, ясно."),
    "referential_math": ("core.referential_math_reply.try_referential_math_reply_sync", "4"),
    "news_direct": ("core.news_reply.try_news_reply_sync", "1. Главное\n2. Второе"),
}


def _step_meta(plan) -> dict:
    """Извлечь meta первого шага plan."""
    inp = (plan.steps[0].args or {}).get("input") or {}
    return inp.get("meta") if isinstance(inp, dict) else {}


class TurnHotPathIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Полный цикл orchestrator с BehaviorStore и TurnContract."""

    async def asyncSetUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self._env = patch.dict(os.environ, _TURN_ENV, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)
        self._planner_direct = patch(
            "core.brain_own_turn.planner_direct_allowed",
            return_value=True,
        )
        self._planner_direct.start()
        self.addCleanup(self._planner_direct.stop)
        self.store = BehaviorStore(base_dir=self._td.name)
        pr = PluginRegistry(self._td.name)
        pe = PolicyEngine()
        self.orch = Orchestrator(
            plugin_registry=pr,
            policy_engine=pe,
            behavior_store=self.store,
        )

    def _input(self, text: str, user_id: str, *, gen: int, trace: str = "trace-hot") -> Input:
        """Собрать Input с generation token."""
        return Input(
            type="text",
            payload=text,
            meta={
                "trace_id": trace,
                "turn_generation": gen,
                "user_id": user_id,
            },
        )

    async def _plan_execute(
        self,
        user_id: str,
        text: str,
        *,
        group_id: str | None = None,
        persisted: dict | None = None,
    ):
        """plan + execute_plan с bump generation."""
        if persisted:
            self.store.save(user_id, group_id, persisted)
        gen = self.store.bump_turn_generation(user_id, group_id)
        inp = self._input(text, user_id, gen=gen)
        plan = self.orch.plan(inp, user_id=user_id, group_id=group_id)
        outputs = await self.orch.execute_plan(plan, user_id=user_id, group_id=group_id)
        return plan, outputs, gen

    async def test_empty_fast_path_plan_execute_turn_contract(self) -> None:
        """Пустой payload: fast path + turn_contract в plan и output meta."""
        uid = "u_empty"
        gen = self.store.bump_turn_generation(uid, None)
        inp = Input(type="text", payload="   ", meta={"trace_id": "t-empty", "turn_generation": gen})
        plan = self.orch.plan(inp, user_id=uid, group_id=None)
        meta = _step_meta(plan)
        self.assertEqual((plan.steps[0].args or {}).get("fallback_variant"), "empty_payload")
        self.assertIsInstance(meta.get("turn_contract"), dict)
        self.assertTrue(str(meta.get("plan_turn_hash") or ""))
        outputs = await self.orch.execute_plan(plan, user_id=uid, group_id=None)
        self.assertEqual(len(outputs), 1)
        out_meta = outputs[0].meta or {}
        self.assertIsInstance(out_meta.get("turn_contract"), dict)
        self.assertEqual(out_meta.get("turn_generation"), gen)

    async def test_weather_mock_defer_finalize_writes_store(self) -> None:
        """Weather direct: defer store до finalize, затем recent_messages."""
        uid = "u_wx"
        target, reply = _DIRECT_MOCKS["weather_direct"]
        with patch(target, return_value=reply):
            plan, outputs, gen = await self._plan_execute(uid, "какая погода в минске")
        meta = _step_meta(plan)
        tc = meta.get("turn_contract") or {}
        self.assertEqual(str(tc.get("short_circuit") or ""), "weather_direct")
        self.assertEqual(tc.get("lane"), "FACT")
        self.assertIn("Минск", outputs[0].payload or "")
        rec_before = self.store.load(uid, None)
        self.assertFalse(rec_before.get("recent_messages"))
        out_meta = dict(outputs[0].meta or {})
        self.assertIsInstance(out_meta.get("pending_turn_store"), dict)
        ok = finalize_delivery_from_output_meta(
            orchestrator=self.orch,
            user_id=uid,
            group_id=None,
            sent_text="В Минске +15°C, ясно.",
            output_meta=out_meta,
        )
        self.assertTrue(ok)
        rec = self.store.load(uid, None)
        self.assertEqual(rec["recent_messages"][-1]["text"], "В Минске +15°C, ясно.")
        self.assertEqual(out_meta.get("turn_generation"), gen)

    async def test_math_direct_execute_output_meta(self) -> None:
        """Referential math shortcut через plan → execute без brain."""
        uid = "u_math"
        target, reply = _DIRECT_MOCKS["referential_math"]
        with patch(target, return_value=reply):
            plan, outputs, _gen = await self._plan_execute(uid, "сколько будет 2+2")
        self.assertEqual(
            (plan.steps[0].args or {}).get("fallback_variant"),
            "referential_math",
        )
        self.assertEqual(outputs[0].payload, "4")
        tc = (_step_meta(plan).get("turn_contract") or {})
        self.assertEqual(tc.get("lane"), "FACT")

    async def test_stale_generation_skips_store_on_execute(self) -> None:
        """Устаревший generation: execute не пишет в behavior_store."""
        uid = "u_stale"
        gen = self.store.bump_turn_generation(uid, None)
        inp = self._input("привет", uid, gen=gen)
        plan = self.orch.plan(inp, user_id=uid, group_id=None)
        self.store.bump_turn_generation(uid, None)
        with patch("core.weather_reply.try_weather_reply_sync", return_value=None):
            outputs = await self.orch.execute_plan(plan, user_id=uid, group_id=None)
        out_meta = outputs[0].meta or {}
        self.assertIsNone(out_meta.get("pending_turn_store"))
        rec = self.store.load(uid, None)
        self.assertFalse(rec.get("recent_messages"))

    async def test_regression_direct_shortcuts_plan_contract(self) -> None:
        """Regression fixture: direct shortcuts получают ожидаемый lane/contract в plan."""
        cases = load_regression_cases()
        if not cases:
            fixture = Path(__file__).resolve().parent / "fixtures" / "turn_regression_cases.json"
            cases = json.loads(fixture.read_text(encoding="utf-8"))
        checked = 0
        for case in cases:
            sc = str(case.get("short_circuit") or "").strip()
            if sc not in _DIRECT_MOCKS:
                continue
            target, reply = _DIRECT_MOCKS[sc]
            uid = f"u_reg_{case.get('id', sc)}"
            persisted: dict = {}
            if case.get("recent_dialogue"):
                persisted["recent_messages"] = list(case["recent_dialogue"])
            if case.get("discourse_resolution"):
                persisted.setdefault("dialogue_state", {})["discourse_resolution"] = case[
                    "discourse_resolution"
                ]
            with patch(target, return_value=reply):
                plan, _outputs, _gen = await self._plan_execute(
                    uid,
                    str(case.get("user_text") or ""),
                    persisted=persisted or None,
                )
            meta = _step_meta(plan)
            tc = meta.get("turn_contract") or {}
            expect = case.get("expect") if isinstance(case.get("expect"), dict) else {}
            self.assertIsInstance(tc, dict, msg=f"case {case.get('id')}")
            self.assertTrue(str(meta.get("plan_turn_hash") or ""), msg=f"case {case.get('id')}")
            if expect.get("lane"):
                self.assertEqual(
                    tc.get("lane"),
                    expect["lane"],
                    msg=f"case {case.get('id')} lane",
                )
            if sc:
                self.assertEqual(
                    str(tc.get("short_circuit") or ""),
                    sc,
                    msg=f"case {case.get('id')} short_circuit",
                )
            checked += 1
        self.assertGreaterEqual(checked, 3, "direct shortcut regression subset")


if __name__ == "__main__":
    unittest.main()
