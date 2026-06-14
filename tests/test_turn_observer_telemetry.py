"""Tests for turn_observer telemetry and issue detection."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.turn_observer import detect_issues, record_from_turn_outcome


class TestTurnObserverTelemetry(unittest.TestCase):
    def test_detect_issues_marks_fallback_and_negative_feedback(self) -> None:
        issues = detect_issues(
            outcome="fallback",
            user_feedback_negative=True,
            user_feedback_positive=False,
            assistant_excerpt="short",
            detail="user_facts_identity_nl",
        )
        self.assertIn("outcome_fallback", issues)
        self.assertIn("user_feedback_negative", issues)

    def test_record_from_turn_outcome_writes_recent_fingerprint_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "turns.jsonl"
            payload = {
                "user_id": "u1",
                "group_id": None,
                "fp": "fallback-fingerprint",
                "intent": "general",
                "module": "brain",
                "profile": "standard",
                "dialogue_lane": "DIALOGUE",
                "outcome": "ok",
                "task_tier": "standard",
                "latency_ms": 10,
                "prompt_tokens_est": 100,
                "brain_recent_limit": 6,
                "completion_tokens": 10,
                "ok": True,
                "user_excerpt": "привет",
                "assistant_excerpt": "здравствуйте",
                "detail": "",
            }
            with mock.patch.dict(
                "os.environ",
                {
                    "GEMMA_TURNS_LOG_PATH": str(log_path),
                    "TURN_OBSERVER_ENABLED": "true",
                    "TURN_OBSERVER_MAX_LINES": "100",
                },
            ):
                record_from_turn_outcome(payload)

            lines = log_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            row = json.loads(lines[0])
            self.assertEqual(row["fp"], "fallback-fingerprint")
            self.assertEqual(row["recent_fingerprint"], "fallback-fingerprint")
            self.assertNotIn("turn_generation", row)

    def test_record_from_turn_outcome_prefers_turn_contract_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "turns.jsonl"
            payload = {
                "user_id": "u2",
                "group_id": None,
                "fp": "raw-fingerprint",
                "intent": "general",
                "module": "brain",
                "profile": "standard",
                "dialogue_lane": "DIALOGUE",
                "outcome": "ok",
                "task_tier": "standard",
                "latency_ms": 10,
                "prompt_tokens_est": 100,
                "brain_recent_limit": 6,
                "completion_tokens": 10,
                "ok": True,
                "user_excerpt": "привет",
                "assistant_excerpt": "здравствуйте",
                "detail": "",
                "turn_contract_audit": {
                    "generation": 7,
                    "referent": "thread",
                    "lane": "DIALOGUE",
                    "recent_fingerprint": "audit-fingerprint",
                    "topic_anchor": "thread:stay",
                    "must_blocks": ["topic_anchor"],
                },
            }
            with mock.patch.dict(
                "os.environ",
                {
                    "GEMMA_TURNS_LOG_PATH": str(log_path),
                    "TURN_OBSERVER_ENABLED": "true",
                    "TURN_OBSERVER_MAX_LINES": "100",
                },
            ):
                record_from_turn_outcome(payload)

            row = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["fp"], "raw-fingerprint")
            self.assertEqual(row["recent_fingerprint"], "audit-fingerprint")
            self.assertEqual(row["turn_generation"], 7)
            self.assertEqual(row["referent"], "thread")
            self.assertEqual(row["must_blocks"], ["topic_anchor"])


if __name__ == "__main__":
    unittest.main()
