import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

from core import usage_learning as ul


class UsageLearningTests(unittest.TestCase):
    def setUp(self) -> None:
        ul.reset_for_tests()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_record_and_snapshot(self):
        with mock.patch.dict(os.environ, {"RESILIENCE_RUNTIME_DIR": self._tmp.name}, clear=False):
            ul.reset_for_tests()
            ul.record_usage("Сколько 2+2?", "math", "math")
            ul.record_usage("Сколько 10+20?", "math", "math")
            snap = ul.snapshot()
        self.assertEqual(snap["total_events"], 2)
        self.assertTrue(snap["top_intents"])
        self.assertEqual(snap["top_intents"][0]["intent"], "math")

    def test_insights_non_empty(self):
        with mock.patch.dict(os.environ, {"RESILIENCE_RUNTIME_DIR": self._tmp.name}, clear=False):
            ul.reset_for_tests()
            ul.record_usage("погода в Москве", "general", "chat-orchestrator")
            rows = ul.insights()
        self.assertTrue(rows)

    def test_persist_reload(self):
        with mock.patch.dict(os.environ, {"RESILIENCE_RUNTIME_DIR": self._tmp.name}, clear=False):
            ul.reset_for_tests()
            for _ in range(30):
                ul.record_usage("ping", "echo", "echo")
            ul.persist_state()
            ul.reset_for_tests()
            ul.ensure_loaded()
            self.assertEqual(ul.snapshot()["total_events"], 30)

    def test_digest_slot_and_checkpoint(self):
        with mock.patch.dict(os.environ, {"RESILIENCE_RUNTIME_DIR": self._tmp.name}, clear=False):
            ul.reset_for_tests()
            ul.record_usage("a", "i1", "m1")
            now = datetime(2026, 5, 1, 8, 5, tzinfo=timezone.utc)
            emit, slot = ul.should_emit_digest_this_hour(now=now, digest_hours=[8, 20])
            self.assertTrue(emit)
            self.assertEqual(slot, "2026-05-01T08")
            ul.commit_digest_checkpoint(slot)
            emit2, _ = ul.should_emit_digest_this_hour(now=now, digest_hours=[8, 20])
            self.assertFalse(emit2)
            payload = ul.build_digest_payload(slot_label=slot)
            self.assertIn("delta_events", payload)
            self.assertIn("trends", payload)
            self.assertIsNone(payload.get("lamp"))


if __name__ == "__main__":
    unittest.main()
