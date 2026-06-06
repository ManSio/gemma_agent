import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

from core.data_governance import DataGovernance


class DataGovernanceTests(unittest.TestCase):
    def test_redact(self):
        dg = DataGovernance()
        out = dg.redact({"token": "abc", "x": 1, "nested": {"password": "123"}})
        self.assertEqual(out["token"], "***REDACTED***")
        self.assertEqual(out["nested"]["password"], "***REDACTED***")
        self.assertEqual(out["x"], 1)

    def test_purge_runtime_logs_full(self):
        dg = DataGovernance()
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "e.jsonl")
            row = {"ts": datetime.now(timezone.utc).isoformat(), "message": "x"}
            with open(path, "w", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
            with mock.patch("core.data_governance._log_path", return_value=path):
                r = dg.purge_runtime_logs(full=True)
            self.assertTrue(r.get("ok"))
            self.assertEqual(r.get("removed"), 1)
            self.assertEqual(r.get("kept"), 0)
            self.assertEqual(r.get("mode"), "full")
            self.assertEqual(open(path, encoding="utf-8").read().strip(), "")


if __name__ == "__main__":
    unittest.main()
