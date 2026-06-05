import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from core.admin_module import AdminModule
from core.error_analysis import record_error_event
from core.reasoning_status import save_reasoning_quality_snapshot


class AdminModuleTests(unittest.TestCase):
    def test_is_admin_includes_notify_ids(self):
        with patch.dict(
            os.environ,
            {"ADMIN_NOTIFY_USER_IDS": "999", "ADMIN_USER_IDS": ""},
            clear=False,
        ):
            m = AdminModule(orchestrator=MagicMock())
            self.assertTrue(m.is_admin("999"))
            self.assertFalse(m.is_admin("1"))

    def test_tail_runtime_errors_empty(self):
        orch = MagicMock()
        orch.get_system_info.return_value = {}
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"ERROR_ANALYSIS_DIR": td}, clear=False):
                m = AdminModule(orchestrator=orch)
                s = m.tail_runtime_errors_text(5)
        self.assertIn("пуст", s.lower())

    def test_admin_logs_snapshot_newest_first_and_filter(self):
        orch = MagicMock()
        orch.get_system_info.return_value = {}
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"ERROR_ANALYSIS_DIR": td}, clear=False):
                record_error_event("voice", "old_v", severity="error")
                record_error_event("brain", "noise")
                record_error_event("voice", "new_v", severity="error")
                m = AdminModule(orchestrator=orch)
                snap = m.admin_logs_snapshot(10, component="voice")
        self.assertIn("new_v", snap["body"])
        self.assertIn("old_v", snap["body"])
        self.assertLess(snap["body"].index("new_v"), snap["body"].index("old_v"))
        self.assertTrue(snap["file_meta"].get("path", "").endswith("runtime_errors.jsonl"))

    def test_full_system_report_has_keys(self):
        orch = MagicMock()
        orch.get_system_info.return_value = {"overall_status": "healthy", "modules": []}
        rc = MagicMock()
        rc.is_enabled.return_value = True
        rc.is_safe_mode.return_value = False
        rc.evaluate.return_value = {"kpi_ok": True, "error_total": 0, "degraded": False, "critical": False}
        orch._resilience = rc
        m = AdminModule(orchestrator=orch)
        rep = m.full_system_report()
        self.assertIn("unified_health", rep)
        self.assertIn("external_services", rep["unified_health"])
        self.assertIn("resilience_evaluate", rep)
        self.assertIn("commands", rep)
        self.assertIn("xray", rep["commands"])
        self.assertIn("diagnostic_zip", rep["commands"])
        self.assertIn("connectivity", rep["commands"])
        self.assertIn("code_map", rep["commands"])

    def test_health_summary_includes_reasoning_quality(self):
        orch = MagicMock()
        orch.get_system_info.return_value = {"overall_status": "healthy", "modules": []}
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"GEMMA_PROJECT_ROOT": td}, clear=False):
                save_reasoning_quality_snapshot(
                    {
                        "intent": "reasoning",
                        "module": "chat-orchestrator",
                        "outcome": "ok",
                        "final_answer_present": True,
                        "reasoning_completed": True,
                        "no_meta_text": True,
                    }
                )
                m = AdminModule(orchestrator=orch)
                hs = m.health_summary()
        self.assertIn("reasoning_quality", hs)
        rq = hs["reasoning_quality"]
        self.assertEqual(rq.get("intent"), "reasoning")
        self.assertTrue(rq.get("final_answer_present"))
        self.assertTrue(rq.get("reasoning_completed"))
        self.assertTrue(rq.get("no_meta_text"))

    def test_reasoning_quality_snapshot_ok_flag(self):
        orch = MagicMock()
        orch.get_system_info.return_value = {"overall_status": "healthy", "modules": []}
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"GEMMA_PROJECT_ROOT": td}, clear=False):
                save_reasoning_quality_snapshot(
                    {
                        "intent": "logic",
                        "module": "chat-orchestrator",
                        "outcome": "ok",
                        "final_answer_present": True,
                        "reasoning_completed": True,
                        "no_meta_text": True,
                    }
                )
                m = AdminModule(orchestrator=orch)
                snap = m.reasoning_quality_snapshot()
        self.assertTrue(snap.get("ok"))


if __name__ == "__main__":
    unittest.main()
