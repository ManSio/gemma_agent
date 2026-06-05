import json
import os
import tempfile
import unittest
from unittest.mock import patch

from core.error_analysis import aggregate_error_stats, read_recent_events, record_error_event


class ErrorAnalysisTests(unittest.TestCase):
    def test_aggregate_counts_only_configured_severities(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"ERROR_ANALYSIS_DIR": td, "RESILIENCE_ERROR_COUNT_SEVERITIES": "error"}, clear=False):
                record_error_event("a", "w", severity="warning")
                record_error_event("b", "e1", severity="error")
                record_error_event("b", "e2", severity="error")
                st = aggregate_error_stats(limit=50)
            self.assertEqual(st["total_all"], 3)
            self.assertEqual(st["total"], 2)
            self.assertEqual(st["by_component"].get("b"), 2)
            self.assertNotIn("a", st["by_component"])

    def test_read_recent_preserves_severity(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"ERROR_ANALYSIS_DIR": td}, clear=False):
                record_error_event("x", "m", severity="info")
                rows = read_recent_events(10)
            self.assertEqual(rows[-1].get("severity"), "info")

    def test_read_recent_component_filter(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"ERROR_ANALYSIS_DIR": td}, clear=False):
                record_error_event("brain", "a")
                record_error_event("voice", "v1")
                record_error_event("brain", "b")
                record_error_event("voice", "v2")
                rows = read_recent_events(5, component="voice")
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0].get("message"), "v1")
            self.assertEqual(rows[1].get("message"), "v2")

    def test_read_recent_sorted_by_ts_not_file_order(self):
        """В файле строки могут идти не по времени; выдача — по полю ts."""
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "runtime_errors.jsonl")
            rows_raw = [
                {"ts": "2026-05-03T12:00:00+00:00", "component": "x", "code": "c", "message": "noon", "severity": "error"},
                {"ts": "2026-05-03T10:00:00+00:00", "component": "x", "code": "c", "message": "morning", "severity": "error"},
            ]
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(json.dumps(r, ensure_ascii=False) for r in rows_raw) + "\n")
            with patch.dict(os.environ, {"ERROR_ANALYSIS_DIR": td}, clear=False):
                rows = read_recent_events(10)
        self.assertEqual([r.get("message") for r in rows], ["morning", "noon"])


if __name__ == "__main__":
    unittest.main()
