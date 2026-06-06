"""METR-style reliability horizon from turns.jsonl."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.research.reliability_horizon import (
    compute_horizon_report,
    horizon_turn_count,
    max_consecutive_success,
    split_sessions,
    turn_is_success,
)


class ReliabilityHorizonTests(unittest.TestCase):
    def test_turn_is_success_ok(self) -> None:
        self.assertTrue(turn_is_success({"outcome": "ok", "issues": []}))

    def test_turn_is_success_clarify_false(self) -> None:
        self.assertFalse(turn_is_success({"outcome": "clarify"}))

    def test_turn_is_success_negative_feedback(self) -> None:
        self.assertFalse(turn_is_success({"outcome": "ok", "user_feedback_negative": True}))

    def test_max_consecutive_success(self) -> None:
        rows = [
            {"outcome": "ok"},
            {"outcome": "ok"},
            {"outcome": "clarify"},
            {"outcome": "ok"},
        ]
        self.assertEqual(max_consecutive_success(rows), 2)

    def test_horizon_turn_count_50pct(self) -> None:
        # streaks 5, 4, 2 — 2/3 sessions >= 4 → horizon 4
        self.assertEqual(horizon_turn_count([5, 4, 2]), 4)
        self.assertEqual(horizon_turn_count([1, 1, 1]), 1)

    def test_split_sessions_by_gap(self) -> None:
        t0 = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(minutes=5)
        t2 = t0 + timedelta(hours=2)
        rows = [
            {"ts": t0.isoformat(), "outcome": "ok"},
            {"ts": t1.isoformat(), "outcome": "ok"},
            {"ts": t2.isoformat(), "outcome": "ok"},
        ]
        sessions = split_sessions(rows, gap_minutes=30)
        self.assertEqual(len(sessions), 2)

    def test_compute_horizon_report_synthetic(self) -> None:
        now = datetime.now(timezone.utc)
        lines = []
        for i in range(6):
            lines.append(
                json.dumps(
                    {
                        "ts": (now - timedelta(hours=i)).isoformat(),
                        "user_id": "u_test",
                        "outcome": "ok",
                        "profile": "standard",
                    },
                    ensure_ascii=False,
                )
            )
        lines.append(
            json.dumps(
                {
                    "ts": (now - timedelta(hours=7)).isoformat(),
                    "user_id": "u_test",
                    "outcome": "clarify",
                    "profile": "standard",
                },
                ensure_ascii=False,
            )
        )
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "turns.jsonl"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            report = compute_horizon_report(path, days=30, session_gap_minutes=120)
        self.assertGreaterEqual(report["horizon_turns_50pct"], 1)
        self.assertGreater(report["sessions_n"], 0)
        self.assertIn("interpretation", report)


if __name__ == "__main__":
    unittest.main()
