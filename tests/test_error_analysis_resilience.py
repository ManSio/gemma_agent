import json
import os
import tempfile
import unittest
from pathlib import Path

from core.error_analysis import aggregate_error_stats, record_error_event


class ErrorAnalysisResilienceTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        os.environ["ERROR_ANALYSIS_DIR"] = self._tmpdir.name
        os.environ["RESILIENCE_ERROR_MAX_AGE_HOURS"] = "48"
        os.environ.pop("RESILIENCE_ERROR_EXCLUDE_MESSAGE_SUBSTR", None)

    def test_resilience_excludes_operational_noise(self):
        record_error_event("resilience", "safe_mode entered: degraded", severity="error")
        record_error_event("resilience", "news_item_search failed", severity="error")
        record_error_event("brain", "tool returned error", severity="error")
        plain = aggregate_error_stats(limit=50)
        filt = aggregate_error_stats(limit=50, for_resilience=True)
        self.assertEqual(plain["total"], 3)
        self.assertEqual(filt["total"], 1)
        self.assertEqual(filt["by_component"].get("brain"), 1)


if __name__ == "__main__":
    unittest.main()
