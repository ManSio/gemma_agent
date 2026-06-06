"""Тесты AutoRollback / UndoLog."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from core.auto_rollback import UndoLog, UndoEntry, AutoRollbackEngine, get_undo_log


class TestUndoLog(unittest.TestCase):
    def setUp(self):
        fd, self.tmp = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        self.log = UndoLog(path=self.tmp)

    def tearDown(self):
        try:
            Path(self.tmp).unlink(missing_ok=True)
        except OSError:
            pass

    def test_add_and_get(self):
        eid = self.log.add("TestHealer", "test_action", {"key": "val"}, verify_window_sec=60)
        self.assertIsNotNone(eid)
        entry = self.log.get(eid)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.healer, "TestHealer")
        self.assertEqual(entry.action, "test_action")
        self.assertEqual(entry.status, "pending")
        self.assertEqual(entry.params["key"], "val")

    def test_confirm(self):
        eid = self.log.add("H", "a", {})
        self.assertTrue(self.log.confirm(eid))
        self.assertEqual(self.log.get(eid).status, "confirmed")
        # Double confirm
        self.assertFalse(self.log.confirm(eid))

    def test_rollback(self):
        eid = self.log.add("H", "a", {})
        self.assertTrue(self.log.rollback(eid, "test reason"))
        entry = self.log.get(eid)
        self.assertEqual(entry.status, "rolled_back")
        self.assertEqual(entry.rollback_reason, "test reason")

    def test_list_pending(self):
        self.log.add("H1", "a", {})
        self.log.add("H2", "a", {})
        self.assertEqual(len(self.log.list_pending()), 2)
        eid = self.log.list_pending()[0].id
        self.log.confirm(eid)
        self.assertEqual(len(self.log.list_pending()), 1)

    def test_pending_ready(self):
        eid = self.log.add("H", "a", {}, verify_window_sec=0.01)
        time.sleep(0.02)
        ready = self.log.pending_ready()
        self.assertTrue(any(e.id == eid for e in ready))

    def test_list_all_limited(self):
        for i in range(10):
            self.log.add(f"H{i}", "a", {})
        self.assertEqual(len(self.log.list_all(limit=5)), 5)

    def test_persistence_reload(self):
        eid = self.log.add("H", "a", {"k": "v"})
        self.log2 = UndoLog(path=self.tmp)
        entry = self.log2.get(eid)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.params["k"], "v")


class TestAutoRollbackEngine(unittest.TestCase):
    def setUp(self):
        fd, self.tmp = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        self.log = UndoLog(path=self.tmp)
        self.engine = AutoRollbackEngine(undo_log=self.log)
        self.engine._cooldown_sec = 0  # no cooldown for tests

    def tearDown(self):
        try:
            Path(self.tmp).unlink(missing_ok=True)
        except OSError:
            pass

    def test_no_pending_does_nothing(self):
        self.engine._last_check = 0
        asyncio_run(self.engine.check_pending())
        # no exception = pass

    def test_unknown_healer_auto_confirms(self):
        eid = self.log.add("UnknownHealer", "some_action", {}, verify_window_sec=0)
        time.sleep(0.01)
        asyncio_run(self.engine.check_pending())
        entry = self.log.get(eid)
        self.assertEqual(entry.status, "confirmed")

    def test_latency_improved_confirms(self):
        eid = self.log.add(
            "AutoLatencyHealer", "set_env",
            {"key": "MODEL_SWITCH_THRESHOLD", "old_p95": 15000, "threshold_ms": 10000,
             "old_value": "8000", "new_value": "12000"},
            verify_window_sec=0,
        )
        time.sleep(0.01)
        with patch("core.observability.OBS.p95", return_value=5000):
            asyncio_run(self.engine.check_pending())
        entry = self.log.get(eid)
        self.assertEqual(entry.status, "confirmed")

    def test_latency_not_improved_rolls_back(self):
        eid = self.log.add(
            "AutoLatencyHealer", "set_env",
            {"key": "MODEL_SWITCH_THRESHOLD", "old_p95": 15000, "threshold_ms": 10000,
             "old_value": "8000", "new_value": "12000"},
            verify_window_sec=0,
        )
        time.sleep(0.01)
        with patch("core.observability.OBS.p95", return_value=14000):
            with patch("core.auto_rollback.AutoRollbackEngine._do_rollback", new_callable=AsyncMock) as mock_rollback:
                asyncio_run(self.engine.check_pending())
                mock_rollback.assert_called_once()

    def test_module_disable_improved_confirms(self):
        eid = self.log.add(
            "ModuleFailureHealer", "auto_disable_module",
            {"module": "bad_mod", "failures": 5, "old_total_fail": 10},
            verify_window_sec=0,
        )
        time.sleep(0.01)
        with patch("core.monitoring.MONITOR.counters", {"module_exec_fail_total": 10}):
            asyncio_run(self.engine.check_pending())
        entry = self.log.get(eid)
        self.assertEqual(entry.status, "confirmed")

    def test_module_disable_worsens_rolls_back(self):
        eid = self.log.add(
            "ModuleFailureHealer", "auto_disable_module",
            {"module": "bad_mod", "failures": 5, "old_total_fail": 10},
            verify_window_sec=0,
        )
        time.sleep(0.01)
        with patch("core.monitoring.MONITOR.counters", {"module_exec_fail_total": 15}):
            with patch("core.auto_rollback.AutoRollbackEngine._do_rollback", new_callable=AsyncMock) as mock_rollback:
                asyncio_run(self.engine.check_pending())
                mock_rollback.assert_called_once()

    def test_do_rollback_setenv(self):
        eid = self.log.add(
            "AutoLatencyHealer", "set_env",
            {"key": "TEST_ROLLBACK_VAR", "old_value": "old_val", "new_value": "new_val"},
        )
        os.environ["TEST_ROLLBACK_VAR"] = "new_val"
        entry = self.log.get(eid)
        asyncio_run(self.engine._do_rollback(entry, "test_rollback"))
        self.assertEqual(os.environ.get("TEST_ROLLBACK_VAR"), "old_val")
        entry2 = self.log.get(eid)
        self.assertEqual(entry2.status, "rolled_back")
        os.environ.pop("TEST_ROLLBACK_VAR", None)

    def test_do_rollback_ephemeral_patch(self):
        eid = self.log.add(
            "ModuleFailureHealer", "create_ephemeral_patch",
            {"module": "test_mod", "failures": 3},
        )
        entry = self.log.get(eid)
        # Patch load_document to return a lesson that matches trigger
        mock_doc = {
            "lessons": [
                {"id": "lesson_1", "trigger": "test_mod", "active": True,
                 "instruction": "skip test_mod", "created_ts": 100.0},
            ]
        }
        with patch("core.ephemeral_lessons.load_document", return_value=mock_doc):
            with patch("core.ephemeral_lessons.deactivate_lesson") as mock_deactivate:
                asyncio_run(self.engine._do_rollback(entry, "admin_test"))
                mock_deactivate.assert_called_once_with("lesson_1")


def asyncio_run(coro):
    """Helper to run async test."""
    import asyncio
    return asyncio.run(coro)


if __name__ == "__main__":
    unittest.main()
