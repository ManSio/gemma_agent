import os
import time
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from core.reminder_dispatch import _reminder_default_tz_name, _user_tz, parse_due_ts


class ReminderTzTests(unittest.TestCase):
    def test_default_tz_is_moscow(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("REMINDER_DEFAULT_TIMEZONE", None)
            self.assertEqual(_reminder_default_tz_name(), "Europe/Moscow")

    def test_parse_today_time_uses_default_tz(self):
        with patch.dict(os.environ, {"REMINDER_DEFAULT_TIMEZONE": "Europe/Moscow"}, clear=False):
            with patch("core.reminder_dispatch._user_tz", return_value="Europe/Moscow"):
                due = parse_due_ts("напомни сегодня в 09:30 позвонить", user_id="u1")
        self.assertIsNotNone(due)
        dt = datetime.fromtimestamp(due, tz=ZoneInfo("Europe/Moscow"))
        self.assertEqual((dt.hour, dt.minute), (9, 30))

    def test_relative_minutes_unchanged(self):
        due = parse_due_ts("напомни через 5 минут вода", user_id="")
        self.assertIsNotNone(due)
        self.assertGreater(due, int(time.time()) + 200)

    def test_explicit_utc_suffix(self):
        with patch("core.reminder_dispatch._user_tz", return_value="Europe/Moscow"):
            due = parse_due_ts("напомни сегодня в 12:00 utc обед", user_id="u1")
        self.assertIsNotNone(due)
        dt_utc = datetime.fromtimestamp(due, tz=ZoneInfo("UTC"))
        self.assertEqual((dt_utc.hour, dt_utc.minute), (12, 0))


if __name__ == "__main__":
    unittest.main()
