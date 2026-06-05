import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.resilience_controller import ResilienceController, expand_module_allowlist_ids


class _FakeOrch:
    def get_system_info(self):
        return {"overall_status": "healthy", "modules": [{"status": "healthy"}]}


class ResilienceControllerTests(unittest.TestCase):
    def test_expand_allowlist_chat_aliases(self):
        self.assertEqual(
            expand_module_allowlist_ids({"chat_orchestrator", "math"}),
            {"chat_orchestrator", "chat-orchestrator", "math"},
        )
        self.assertEqual(expand_module_allowlist_ids({"echo"}), {"echo"})

    def test_safe_mode_allowlist_includes_hyphen_chat(self):
        os.environ["SAFE_MODE_MODULE_ALLOWLIST"] = "chat_orchestrator,math,echo"
        rc = ResilienceController()
        al = rc.safe_mode_allowlist()
        self.assertIn("chat-orchestrator", al)
        self.assertIn("math", al)

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._rt = Path(self._tmpdir.name)
        os.environ["RESILIENCE_RUNTIME_DIR"] = str(self._rt)
        os.environ["ERROR_ANALYSIS_DIR"] = str(self._rt / "error_analysis")
        os.environ["RESILIENCE_AUTONOMY_ENABLED"] = "true"
        os.environ["RESILIENCE_SAFE_ERROR_TOTAL"] = "5"
        os.environ["RESILIENCE_CRITICAL_ERROR_TOTAL"] = "20"
        os.environ["RESILIENCE_CRITICAL_FAILED_MODULES"] = "3"
        os.environ["RESILIENCE_RECOVERY_OK_CYCLES"] = "2"

    def test_restart_request_journals_once_and_not_as_error(self):
        rc = ResilienceController()
        sev: list = []

        def cap(c, m, **kw):
            sev.append(str(kw.get("severity") or "error"))

        with patch("core.resilience_controller.record_error_event", side_effect=cap):
            rc.request_container_restart("first")
            rc.request_container_restart("second")
        self.assertEqual(sev, ["info"])
        data = json.loads((self._rt / "restart_requested.json").read_text(encoding="utf-8"))
        self.assertIn("second", str(data.get("reason")))

    def test_acknowledge_restart_clears_flag(self):
        rc = ResilienceController()
        rc.request_container_restart("test")
        self.assertTrue((self._rt / "restart_requested.json").is_file())
        ack = rc.acknowledge_restart_if_pending()
        self.assertIsNotNone(ack)
        self.assertFalse((self._rt / "restart_requested.json").is_file())
        self.assertTrue((self._rt / "restart_acknowledged.json").is_file())

    def test_post_boot_clears_safe_mode_when_healthy(self):
        rc = ResilienceController()
        rc.enter_safe_mode("test", level="safe")
        self.assertTrue(rc.is_safe_mode())
        orch = _FakeOrch()
        with patch.object(rc, "evaluate", return_value={"degraded": False, "critical": False}):
            rc.post_boot_recovery(orch)
        self.assertFalse(rc.is_safe_mode())

    def test_post_boot_does_not_crash_when_evaluate_returns_error_dict(self):
        rc = ResilienceController()
        rc.enter_safe_mode("test", level="safe")
        orch = _FakeOrch()
        with patch.object(rc, "evaluate", return_value={"error": "synthetic"}):
            out = rc.post_boot_recovery(orch)
        self.assertTrue(rc.is_safe_mode())
        self.assertEqual((out.get("evaluate") or {}).get("error"), "synthetic")

    def test_tick_critical_requests_restart_and_safe_mode(self):
        rc = ResilienceController()
        orch = _FakeOrch()
        with patch.object(rc, "evaluate", return_value={"critical": True, "degraded": True, "error_total": 99, "failed_modules": 0, "kpi_ok": False}):
            with patch("core.resilience_controller.rollback_passport_to_latest_backup", return_value={"ok": True}):
                out = rc.tick(orch, maintenance_ran=True)
        self.assertTrue(out.get("ran"))
        self.assertTrue(rc.is_safe_mode())
        self.assertTrue((self._rt / "restart_requested.json").is_file())

    def test_tick_healthy_exits_safe_mode_after_streak(self):
        rc = ResilienceController()
        orch = _FakeOrch()
        rc.enter_safe_mode("test", level="safe")
        with patch.object(
            rc,
            "evaluate",
            return_value={"critical": False, "degraded": False, "error_total": 0, "failed_modules": 0, "kpi_ok": True},
        ):
            rc.tick(orch, maintenance_ran=True)
            self.assertTrue(rc.is_safe_mode())
            rc.tick(orch, maintenance_ran=True)
        self.assertFalse(rc.is_safe_mode())

    def test_tick_healthy_clears_stale_restart_flag(self):
        rc = ResilienceController()
        orch = _FakeOrch()
        rc.request_container_restart("synthetic")
        self.assertTrue((self._rt / "restart_requested.json").is_file())
        with patch.object(
            rc,
            "evaluate",
            return_value={"critical": False, "degraded": False, "error_total": 0, "failed_modules": 0, "kpi_ok": True},
        ):
            out = rc.tick(orch, maintenance_ran=True)
        self.assertTrue(out.get("restart_flag_cleared"))
        self.assertFalse((self._rt / "restart_requested.json").is_file())

    def test_tick_degraded_resets_recovery_streak_in_safe_mode(self):
        rc = ResilienceController()
        orch = _FakeOrch()
        rc.enter_safe_mode("test", level="safe")
        st_path = self._rt / "safe_mode_state.json"
        data = json.loads(st_path.read_text(encoding="utf-8"))
        data["recovery_ok_streak"] = 1
        st_path.write_text(json.dumps(data), encoding="utf-8")
        with patch.object(
            rc,
            "evaluate",
            return_value={
                "critical": False,
                "degraded": True,
                "error_total": 10,
                "failed_modules": 0,
                "kpi_ok": False,
                "stop_rule_violations": [],
            },
        ):
            rc.tick(orch, maintenance_ran=True)
        st = json.loads(st_path.read_text(encoding="utf-8"))
        self.assertEqual(st.get("recovery_ok_streak"), 0)


if __name__ == "__main__":
    unittest.main()
