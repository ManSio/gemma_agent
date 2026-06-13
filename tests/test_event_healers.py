"""Тесты healers событийной шины."""
import asyncio
import os
import time
import unittest
from unittest.mock import patch, AsyncMock

from core.event_healers import (
    ModuleFailureHealer,
    BugContextGatherer,
    AnomalyEscalator,
    AutoLatencyHealer,
    AutoFailRatioHealer,
    AutoHostPressureHealer,
    healers_snapshot,
    install_healers,
    get_module_failure_healer,
)
from core.event_bus import bus


class TestModuleFailureHealer(unittest.TestCase):
    def setUp(self):
        self.healer = ModuleFailureHealer(max_failures=3)
        self.healer._auto_disable_at = 5

    def test_single_failure_no_patch(self):
        self.healer._failures.clear()
        self.healer._patches_created.clear()
        asyncio.run(self.healer({
            "module_name": "test_mod",
            "ok": False,
            "error": "try again",
        }))
        snap = self.healer.snapshot()
        self.assertEqual(snap["failures"].get("test_mod"), 1)
        self.assertEqual(len(snap["patches_created"]), 0)

    def test_three_failures_triggers_patch(self):
        self.healer._failures.clear()
        self.healer._patches_created.clear()
        self.healer._max_failures = 3

        async def fire():
            for _ in range(3):
                await self.healer({
                    "module_name": "doomed_mod",
                    "ok": False,
                    "error": "fail",
                })
        asyncio.run(fire())

        snap = self.healer.snapshot()
        self.assertIn("doomed_mod", snap["patches_created"])

    def test_five_failures_triggers_auto_disable(self):
        self.healer._failures.clear()
        self.healer._patches_created.clear()
        self.healer._disabled.clear()
        self.healer._max_failures = 3
        self.healer._auto_disable_at = 5

        async def fire():
            for _ in range(5):
                await self.healer({
                    "module_name": "auto_disable_mod",
                    "ok": False,
                    "error": "fail",
                })
        asyncio.run(fire())

        snap = self.healer.snapshot()
        self.assertIn("auto_disable_mod", snap["disabled"])
        self.assertIn("auto_disable_mod", snap["patches_created"])

    def test_success_resets_counter(self):
        self.healer._failures.clear()
        self.healer._patches_created.clear()
        self.healer._max_failures = 3

        async def fire():
            for _ in range(2):
                await self.healer({
                    "module_name": "resetting_mod",
                    "ok": False,
                    "error": "fail",
                })
            await self.healer({
                "module_name": "resetting_mod",
                "ok": True,
                "duration_ms": 50,
                "error": None,
            })
        asyncio.run(fire())

        snap = self.healer.snapshot()
        self.assertEqual(snap["failures"].get("resetting_mod"), None)
        self.assertNotIn("resetting_mod", snap["patches_created"])

    def test_success_resets_disabled(self):
        self.healer._disabled["was_disabled_mod"] = time.time()
        async def fire():
            await self.healer({
                "module_name": "was_disabled_mod",
                "ok": True,
                "duration_ms": 50,
                "error": None,
            })
        asyncio.run(fire())
        snap = self.healer.snapshot()
        self.assertNotIn("was_disabled_mod", snap["disabled"])

    def test_reset_all(self):
        self.healer._failures["a"] = 3
        self.healer._failures["b"] = 5
        self.healer._patches_created.add("b")
        self.healer._disabled["b"] = time.time()
        self.healer.reset()
        snap = self.healer.snapshot()
        self.assertEqual(snap["failures"], {})
        self.assertEqual(snap["patches_created"], [])
        self.assertEqual(snap["disabled"], [])

    def test_reset_one_module(self):
        self.healer._failures["a"] = 3
        self.healer._failures["b"] = 5
        self.healer._patches_created.add("a")
        self.healer.reset("a")
        snap = self.healer.snapshot()
        self.assertNotIn("a", snap["failures"])
        self.assertIn("b", snap["failures"])


class TestAnomalyEscalator(unittest.TestCase):
    def setUp(self):
        self.escalator = AnomalyEscalator()
        self.escalator._window_sec = 300
        self.escalator._max_anomalies = 5
        self.escalator._recent.clear()

    def test_snapshot_no_anomalies(self):
        snap = self.escalator.snapshot()
        self.assertEqual(snap["recent"], {})
        self.assertEqual(snap["max_anomalies"], 5)

    def test_snapshot_with_recent(self):
        now = time.time()
        self.escalator._recent["test_code"] = [now, now - 10, now - 100]
        snap = self.escalator.snapshot()
        self.assertEqual(snap["recent"].get("test_code"), 3)


class TestAutoLatencyHealer(unittest.TestCase):
    def setUp(self):
        self.healer = AutoLatencyHealer()
        self.healer._p95_threshold_ms = 5000
        self.healer._cooldown_sec = 0  # no cooldown for tests
        self.healer._latencies_ms.clear()
        self.healer._last_action = 0

    def test_snapshot_empty(self):
        snap = self.healer.snapshot()
        self.assertEqual(snap["samples"], 0)
        self.assertEqual(snap["p95_ms"], 0.0)

    def test_below_threshold_no_action(self):
        for _ in range(50):
            asyncio.run(self.healer({"latency_ms": 100, "ok": True}))
        snap = self.healer.snapshot()
        self.assertEqual(snap["actions_taken"], 0)

    def test_above_threshold_triggers_action(self):
        for _ in range(50):
            asyncio.run(self.healer({"latency_ms": 15000, "ok": True}))
        snap = self.healer.snapshot()
        self.assertGreater(snap["p95_ms"], 5000)


class TestAutoFailRatioHealer(unittest.TestCase):
    def setUp(self):
        self.healer = AutoFailRatioHealer()
        self.healer._fail_ratio_threshold = 0.3
        self.healer._cooldown_sec = 0
        self.healer._window.clear()
        self.healer._last_action = 0

    def test_snapshot_empty(self):
        snap = self.healer.snapshot()
        self.assertEqual(snap["samples"], 0)

    def test_low_fail_ratio_no_action(self):
        for _ in range(50):
            asyncio.run(self.healer({"ok": True}))
        snap = self.healer.snapshot()
        self.assertEqual(snap["actions_taken"], 0)

    def test_high_fail_ratio_increments_actions(self):
        for _ in range(50):
            asyncio.run(self.healer({"ok": False}))
        snap = self.healer.snapshot()
        self.assertGreater(snap["actions_taken"], 0)


class TestAutoHostPressureHealer(unittest.TestCase):
    def setUp(self):
        self.healer = AutoHostPressureHealer()
        self.healer._cooldown_sec = 0
        self.healer._last_action = 0

    def test_snapshot_structure(self):
        snap = self.healer.snapshot()
        self.assertIn("actions_taken", snap)
        self.assertIn("cooldown_sec", snap)

    def test_no_pressure_no_action(self):
        with patch(
            "core.host_resources.get_host_resource_snapshot",
            return_value={"pressure": {"level": "ok", "reasons": []}, "available": True},
        ):
            with patch("core.host_resources.resource_pressure_escalation_enabled", return_value=False):
                with patch("core.host_resources.resource_pressure_degrades_system", return_value=False):
                    asyncio.run(self.healer({"interval_sec": 300}))
        snap = self.healer.snapshot()
        self.assertEqual(snap["actions_taken"], 0)

    def test_critical_pressure_applies_heavy_modules_env(self):
        with patch(
            "core.host_resources.get_host_resource_snapshot",
            return_value={
                "pressure": {"level": "critical", "reasons": ["mem"]},
                "available": True,
            },
        ):
            with patch("core.host_resources.resource_pressure_escalation_enabled", return_value=True):
                with patch("core.host_resources.resource_pressure_degrades_system", return_value=True):
                    with patch("core.heal_executor.apply_steps", new_callable=AsyncMock) as mock_apply:
                        mock_apply.return_value = {"ok": True, "summary": "ok"}
                        with patch.dict(os.environ, {"HEALERS_ENV_MUTATION_ENABLED": "true"}, clear=False):
                            asyncio.run(self.healer({"interval_sec": 300}))
        mock_apply.assert_awaited_once()
        step = mock_apply.await_args.args[0][0]
        self.assertIn("HEAVY_MODULES_UNDER_PRESSURE", step)


class TestHealersRegistration(unittest.TestCase):
    def tearDown(self):
        bus._subscribers.clear()
        bus._async_subs.clear()

    def test_install_healers_idempotent(self):
        install_healers()
        cnt1 = bus.subscriber_count()
        install_healers()
        cnt2 = bus.subscriber_count()
        self.assertEqual(cnt1, cnt2)

    def test_healers_snapshot_has_all_keys(self):
        snap = healers_snapshot()
        self.assertIn("module_failure_healer", snap)
        self.assertIn("anomaly_escalator", snap)
        self.assertIn("auto_latency_healer", snap)
        self.assertIn("auto_fail_ratio_healer", snap)
        self.assertIn("auto_host_pressure_healer", snap)
        self.assertIn("installed", snap)

    def test_get_module_failure_healer_returns_instance(self):
        mh = get_module_failure_healer()
        self.assertIsNotNone(mh)
        snap = mh.snapshot()
        self.assertIn("failures", snap)
        self.assertIn("disabled", snap)
        self.assertIn("auto_disable_at", snap)

    def test_install_adds_new_subscribers(self):
        install_healers()
        cnt = bus.subscriber_count()
        self.assertIn("openrouter.done", cnt)
        # 2 подписчика на openrouter.done: latency + fail_ratio
        self.assertGreaterEqual(cnt["openrouter.done"], 2)
