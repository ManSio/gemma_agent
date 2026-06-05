import unittest
from unittest.mock import patch

from core.efficiency_report import build_efficiency_snapshot
from core.monitoring import MONITOR


class EfficiencyReportTests(unittest.TestCase):
    def test_efficiency_snapshot_has_expected_blocks(self):
        MONITOR.counters.clear()
        MONITOR.inc("auto_reasoning_est_saved_tokens_total", 200)
        MONITOR.inc("auto_reasoning_est_baseline_tokens_total", 500)
        MONITOR.inc("module_exec_total", 10)
        MONITOR.inc("module_exec_ok_total", 8)
        MONITOR.inc("module_exec_fail_total", 2)
        MONITOR.inc("planner_decisions_total", 20)
        MONITOR.inc("planner_fallback_total", 5)
        with patch("core.efficiency_report.aggregate_usage", return_value={"total_tokens": 1234, "daily_avg_tokens": 100.0, "cost_sum": 0.01, "daily_avg_cost": 0.001, "monthly_est_cost": 0.03}):
            snap = build_efficiency_snapshot(days=7.0, orchestrator=None)
        self.assertIn("token_saving", snap)
        self.assertEqual(snap["token_saving"]["efficiency_percent"], 40.0)
        self.assertIn("plugins", snap)
        self.assertEqual(snap["plugins"]["exec_success_percent"], 80.0)
        self.assertIn("planner", snap)
        self.assertEqual(snap["planner"]["route_success_percent"], 75.0)


if __name__ == "__main__":
    unittest.main()
