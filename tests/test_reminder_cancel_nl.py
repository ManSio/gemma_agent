import os
import tempfile
import unittest
from pathlib import Path

from core.reminder_dispatch import (
    add_recurring_reminder,
    add_reminder,
    cancel_user_reminders,
    load_reminders,
    save_reminders,
)
import time as _time
from core.reminder_nl import (
    looks_like_cancel_reminder_request,
    try_cancel_natural_reminder,
    try_schedule_natural_reminder,
)


class ReminderCancelNLTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["GEMMA_PROJECT_ROOT"] = self._tmpdir.name
        os.environ["REMINDER_NL_ENABLED"] = "true"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_looks_like_cancel(self):
        self.assertTrue(looks_like_cancel_reminder_request("Отмени напоминание это"))
        self.assertFalse(looks_like_cancel_reminder_request("Промокоды действительно есть"))

    def test_cancel_recurring_daily_codes(self):
        uid = "u_cancel_codes"
        add_recurring_reminder(
            uid,
            "собирать информацию по актуальным кодам",
            dows=set(range(7)),
            hour=20,
            minute=0,
        )
        res = try_cancel_natural_reminder(uid, "Отмени напоминание это")
        self.assertIsNotNone(res)
        self.assertTrue(res.get("ok"))
        self.assertIn("Отменил", res.get("reply", ""))
        self.assertIn("код", res.get("reply", "").lower())
        data = load_reminders()
        self.assertEqual(data.get("users", {}).get(uid), [])

    def test_cancel_does_not_schedule(self):
        res = try_schedule_natural_reminder("u_x", "Отмени напоминание это")
        self.assertIsNone(res)

    def test_cancel_hint_pro_test_matches_quoted_label(self):
        uid = "u_pro_test"
        due = int(_time.time()) + 3600
        add_reminder(uid, "сказать «тест пройден»", due)
        res = try_cancel_natural_reminder(uid, "Отмени напоминание про тест")
        self.assertIsNotNone(res)
        self.assertTrue(res.get("ok"))
        self.assertIn("тест", res.get("reply", "").lower())
        data = load_reminders()
        self.assertEqual(data.get("users", {}).get(uid), [])

    def test_cancel_by_list_number_nl(self):
        uid = "u_by_num"
        due = int(_time.time()) + 7200
        add_reminder(uid, "первое", due)
        add_reminder(uid, "второе важное", due + 60)
        res = try_cancel_natural_reminder(uid, "Отмени напоминание 2")
        self.assertIsNotNone(res)
        self.assertTrue(res.get("ok"))
        self.assertEqual(res.get("cancelled"), 1)
        data = load_reminders()
        left = data.get("users", {}).get(uid) or []
        self.assertEqual(len(left), 1)
        self.assertIn("первое", left[0].get("text", ""))
        self.assertIn("второе", (res.get("labels") or [""])[0].lower())

    def test_cancel_user_reminders_latest_only(self):
        uid = "u_latest"
        add_recurring_reminder(uid, "старая задача", dows={0}, hour=9, minute=0)
        add_recurring_reminder(uid, "новая задача", dows={1}, hour=10, minute=0)
        n, labels = cancel_user_reminders(uid, latest_only=True)
        self.assertEqual(n, 1)
        self.assertIn("новая", labels[0].lower())

    def test_cancel_vague_multi_shows_list_hint(self):
        uid = "u_vague_multi"
        due = int(_time.time()) + 3600
        add_reminder(uid, "первое", due)
        add_reminder(uid, "второе", due + 120)
        res = try_cancel_natural_reminder(uid, "Отмени напоминание")
        self.assertIsNotNone(res)
        self.assertFalse(res.get("ok"))
        reply = res.get("reply", "")
        self.assertIn("/rdel", reply)
        self.assertIn("2", reply)
        data = load_reminders()
        self.assertEqual(len(data.get("users", {}).get(uid) or []), 2)


if __name__ == "__main__":
    unittest.main()
