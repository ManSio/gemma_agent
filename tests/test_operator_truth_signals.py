import unittest
from unittest.mock import MagicMock

from core.monitoring import MONITOR
from core.operator_truth_signals import (
    compute_signals,
    maybe_attach_operator_truth_signals,
    user_asks_operator_health,
    user_reports_truncation,
)


class OperatorTruthSignalsTests(unittest.TestCase):
    def tearDown(self) -> None:
        MONITOR.counters.clear()

    def test_user_asks_health(self):
        self.assertTrue(user_asks_operator_health("всё нормально с ботом?"))
        self.assertTrue(user_asks_operator_health("диагностика"))
        self.assertFalse(user_asks_operator_health("привет"))

    def test_truncation_phrase(self):
        self.assertTrue(user_reports_truncation("ответ обрезан"))
        self.assertFalse(user_reports_truncation("как дела"))

    def test_compute_signals_counts_fallback(self):
        orch = MagicMock()
        orch.plugin_registry.loaded_modules = {"chat-orchestrator": object()}
        orch._resilience = MagicMock()
        orch._resilience.is_safe_mode.return_value = False
        MONITOR.inc("planner_fallback_total", 3)
        s = compute_signals(orch)
        self.assertEqual(s["planner_fallback_total"], 3)
        self.assertIn("planner_fallback", s["issues"])

    def test_attach_for_non_admin_truncation(self):
        orch = MagicMock()
        orch.plugin_registry.loaded_modules = {"chat-orchestrator": object()}
        orch._resilience = MagicMock()
        orch._resilience.is_safe_mode.return_value = False
        ctx: dict = {}
        maybe_attach_operator_truth_signals(
            ctx,
            orchestrator=orch,
            user_text="текст обрезан посередине",
            is_admin=False,
        )
        self.assertIn("operator_truth_signals_hint", ctx)


if __name__ == "__main__":
    unittest.main()
