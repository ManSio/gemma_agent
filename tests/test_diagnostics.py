import unittest

from core.diagnostics import build_diagnostic_snapshot
from core.resilience_controller import ResilienceController


class _FakeOrchestrator:
    def get_system_info(self):
        return {"overall_status": "healthy", "planner": {"engine": "unified_planner_v1"}, "modules": []}


class _OrchestratorWithResilience(_FakeOrchestrator):
    """Как прод: evaluate() тянет build_diagnostic_snapshot — не должно быть рекурсии."""

    def __init__(self) -> None:
        self._resilience = ResilienceController()


class DiagnosticsTests(unittest.TestCase):
    def test_build_diagnostic_snapshot_shape(self):
        snap = build_diagnostic_snapshot(_FakeOrchestrator())
        self.assertIn("ts", snap)
        self.assertIn("system", snap)
        self.assertIn("monitoring", snap)
        self.assertIn("observability", snap)
        self.assertIn("errors", snap)
        self.assertIn("governance", snap)
        self.assertIn("knowledge", snap)
        self.assertIn("security", snap)
        self.assertIn("plugin_dependencies", snap)
        self.assertIn("pip_merged", snap["plugin_dependencies"])
        self.assertIn("external_services", snap)
        self.assertEqual(snap["system"]["overall_status"], "healthy")

    def test_build_diagnostic_snapshot_with_resilience_no_recursion(self):
        snap = build_diagnostic_snapshot(_OrchestratorWithResilience())
        self.assertIn("system", snap)
        self.assertEqual(snap["system"]["overall_status"], "healthy")


if __name__ == "__main__":
    unittest.main()
