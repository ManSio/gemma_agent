"""Tests for fingerprint stall alert (Phase 0.4)."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.turn_fingerprint_alert import scan_fingerprint_stalls


class TestTurnFingerprintAlert(unittest.TestCase):
    def test_detects_stall(self) -> None:
        now = datetime.now(timezone.utc)
        fp = "abc123deadbeef01"
        rows = []
        for i in range(4):
            rows.append(
                {
                    "ts": (now - timedelta(minutes=10 - i * 2)).isoformat(),
                    "user_id": "591226766",
                    "recent_fingerprint": fp,
                    "trace_id": f"t{i}",
                }
            )
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
            path = Path(f.name)
        try:
            alerts = scan_fingerprint_stalls(path=path, stall_minutes=5.0)
            self.assertTrue(alerts)
            self.assertEqual(alerts[0].get("fingerprint"), fp)
        finally:
            path.unlink(missing_ok=True)

    def test_no_stall_single_turn(self) -> None:
        now = datetime.now(timezone.utc)
        row = {
            "ts": now.isoformat(),
            "user_id": "1",
            "recent_fingerprint": "solo_fp",
            "trace_id": "t0",
        }
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
            path = Path(f.name)
        try:
            alerts = scan_fingerprint_stalls(path=path, stall_minutes=5.0)
            self.assertEqual(alerts, [])
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
