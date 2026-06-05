import json
import logging
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from core.logging_setup import JsonFormatter
from core.report_timezone import (
    format_health_snapshot_caption,
    format_operator_datetime,
    format_operator_datetime_from_iso,
    format_usage_digest_slot_caption,
    get_report_tz,
    report_time_uses_utc_wall,
    report_utc_offset_label,
)


class ReportTimezoneTests(unittest.TestCase):
    def test_default_utc(self):
        with patch.dict(
            os.environ,
            {"GEMMA_REPORT_TIMEZONE": "", "GEMMA_LOG_TIMEZONE": "", "LOG_TIMEZONE": ""},
            clear=False,
        ):
            self.assertTrue(report_time_uses_utc_wall())
            self.assertIs(get_report_tz(), timezone.utc)

    def test_minsk_offset(self):
        with patch.dict(os.environ, {"GEMMA_REPORT_TIMEZONE": "Europe/Minsk"}, clear=False):
            tz = get_report_tz()
            self.assertFalse(report_time_uses_utc_wall())
            self.assertNotEqual(str(tz), "UTC")

    def test_operator_datetime_no_utc_suffix(self):
        dt = datetime(2026, 5, 3, 12, 0, tzinfo=timezone.utc)
        with patch.dict(os.environ, {"GEMMA_REPORT_TIMEZONE": ""}, clear=False):
            s = format_operator_datetime(dt)
            self.assertIn("03.05.2026", s)
            self.assertIn("12:00", s)
            self.assertNotIn("UTC", s)

    def test_operator_datetime_from_iso_z(self):
        with patch.dict(os.environ, {"GEMMA_REPORT_TIMEZONE": ""}, clear=False):
            s = format_operator_datetime_from_iso("2026-05-03T15:11:52Z")
            self.assertEqual(s, "15:11 · 03.05.2026")

    def test_operator_datetime_from_iso_strips_subsecond(self):
        with patch.dict(os.environ, {"GEMMA_REPORT_TIMEZONE": ""}, clear=False):
            s = format_operator_datetime_from_iso("2026-05-03T15:11:52.999999+00:00")
            self.assertEqual(s, "15:11 · 03.05.2026")

    def test_operator_datetime_from_iso_accepts_datetime(self):
        dt = datetime(2026, 5, 3, 15, 11, 7, 422423, tzinfo=timezone.utc)
        with patch.dict(os.environ, {"GEMMA_REPORT_TIMEZONE": ""}, clear=False):
            s = format_operator_datetime_from_iso(dt)
        self.assertEqual(s, "15:11 · 03.05.2026")

    def test_digest_slot_caption_utc_wall(self):
        with patch.dict(
            os.environ,
            {"GEMMA_REPORT_TIMEZONE": "", "GEMMA_LOG_TIMEZONE": "", "LOG_TIMEZONE": ""},
            clear=False,
        ):
            s = format_usage_digest_slot_caption("2026-05-03T20")
        self.assertIn("2026-05-03", s)
        self.assertIn("20:00", s)
        self.assertIn("UTC", s)
        self.assertIn("дайджест", s)

    def test_digest_slot_caption_minsk_matches_utc_slot(self):
        with patch.dict(os.environ, {"GEMMA_REPORT_TIMEZONE": "Europe/Minsk"}, clear=False):
            s = format_usage_digest_slot_caption("2026-05-03T20")
        self.assertIn("20:00", s)
        self.assertIn("UTC", s)
        self.assertNotIn("UTC+3", s)
        self.assertNotIn("23:00", s)

    def test_health_snapshot_caption_utc_wall(self):
        with patch.dict(os.environ, {"GEMMA_REPORT_TIMEZONE": ""}, clear=False):
            cap = format_health_snapshot_caption("2026-05-03T19:58:02.692415+00:00")
        self.assertEqual(cap, "19:58 · 03.05.2026")
        self.assertNotIn("UTC+", cap)

    def test_utc_offset_minsk(self):
        with patch.dict(os.environ, {"GEMMA_REPORT_TIMEZONE": "Europe/Minsk"}, clear=False):
            self.assertEqual(report_utc_offset_label(), "UTC+3")

    def test_health_snapshot_caption_minsk(self):
        with patch.dict(os.environ, {"GEMMA_REPORT_TIMEZONE": "Europe/Minsk"}, clear=False):
            cap = format_health_snapshot_caption("2026-05-03T19:58:02.692415+00:00")
        self.assertEqual(cap, "22:58 · 03.05.2026")
        self.assertNotIn("UTC+", cap)
        self.assertNotIn("Europe/", cap)

    def test_json_formatter_respects_tz(self):
        fmt = JsonFormatter()
        record = logging.LogRecord(
            name="t",
            level=logging.INFO,
            pathname="x",
            lineno=1,
            msg="m",
            args=(),
            exc_info=None,
        )
        record.created = 1_700_000_000.0
        with patch.dict(os.environ, {"GEMMA_REPORT_TIMEZONE": "Europe/Minsk"}, clear=False):
            line = fmt.format(record)
        d = json.loads(line)
        self.assertIn("ts", d)
        self.assertIn("2023", d["ts"])


if __name__ == "__main__":
    unittest.main()
