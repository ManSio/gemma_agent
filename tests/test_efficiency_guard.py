import unittest
from unittest.mock import patch

from core.efficiency_guard import build_efficiency_guard_patch


class EfficiencyGuardTests(unittest.TestCase):
    def test_warn_level_patch(self):
        snap = {
            "planner": {"route_success_percent": 82.0, "decisions_total": 100},
            "plugins": {"exec_success_percent": 95.0, "exec_total": 100},
            "token_saving": {"efficiency_percent": 20.0},
        }
        with patch("core.efficiency_guard.build_efficiency_snapshot", return_value=snap):
            p = build_efficiency_guard_patch(orchestrator=None, days=7.0)
        self.assertEqual(p.get("level"), "warn")
        self.assertEqual(p.get("task_tier_ceiling"), "nested")
        self.assertTrue(p.get("strict_routing_guard"))

    def test_no_data_stays_ok_not_critical(self):
        snap = {
            "planner": {"route_success_percent": 0.0, "decisions_total": 0},
            "plugins": {"exec_success_percent": 0.0, "exec_total": 0},
            "token_saving": {"efficiency_percent": 0.0},
        }
        with patch("core.efficiency_guard.build_efficiency_snapshot", return_value=snap):
            p = build_efficiency_guard_patch(orchestrator=None, days=7.0)
        self.assertEqual(p.get("level"), "ok")
        self.assertTrue(p.get("insufficient_data"))
        self.assertNotIn("disable_tools_for_general", p)

    def test_critical_level_patch(self):
        snap = {
            "planner": {"route_success_percent": 60.0, "decisions_total": 100},
            "plugins": {"exec_success_percent": 70.0, "exec_total": 100},
            "token_saving": {"efficiency_percent": 3.0},
        }
        with patch("core.efficiency_guard.build_efficiency_snapshot", return_value=snap):
            p = build_efficiency_guard_patch(orchestrator=None, days=7.0)
        self.assertEqual(p.get("level"), "critical")
        self.assertEqual(p.get("task_tier_ceiling"), "shallow")
        self.assertTrue(p.get("disable_tools_for_general"))


if __name__ == "__main__":
    unittest.main()
