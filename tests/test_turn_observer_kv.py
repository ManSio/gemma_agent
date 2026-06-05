"""KV debug fields in turns.jsonl via turn_observer."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.models import Plan, PlanStep
from core.orchestrator import _brain_telemetry_from_plan
from core.turn_observer import record_from_turn_outcome


class TurnObserverKvTests(unittest.TestCase):
    def test_kv_fields_in_turn_record(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "turns.jsonl"
            with patch.dict(os.environ, {"GEMMA_TURNS_LOG_PATH": str(path)}, clear=False):
                record_from_turn_outcome(
                    {
                        "user_id": "u1",
                        "outcome": "ok",
                        "profile": "standard",
                        "kv_session_debug": {
                            "session_id": "u-u1.e3.standard",
                            "epoch": 3,
                            "last_reset_reason": "ttl_expired",
                            "profile_sticky_applied": True,
                            "kv_profile": "standard",
                        },
                    }
                )
            line = path.read_text(encoding="utf-8").strip()
            row = json.loads(line)
            self.assertEqual(row.get("kv_session_id"), "u-u1.e3.standard")
            self.assertEqual(row.get("kv_epoch"), 3)
            self.assertEqual(row.get("kv_reset_reason"), "ttl_expired")
            self.assertTrue(row.get("kv_profile_sticky"))
            self.assertEqual(row.get("kv_profile"), "standard")

    def test_brain_telemetry_from_plan_steps(self) -> None:
        plan = Plan(
            mode="full",
            steps=[
                PlanStep(
                    module_name="echo",
                    args={"context": {"dialogue_state": {"prompt_tokens_est": 100}}},
                ),
                PlanStep(
                    module_name="chat-orchestrator",
                    args={
                        "context": {
                            "dialogue_state": {
                                "prompt_tokens_est": 2400,
                                "brain_recent_limit": 12,
                                "brain_profile": "standard",
                            }
                        }
                    },
                ),
            ]
        )
        tel = _brain_telemetry_from_plan(plan)
        self.assertEqual(tel.get("prompt_tokens_est"), 2400)
        self.assertEqual(tel.get("brain_recent_limit"), 12)

    def test_brain_telemetry_from_brain_turn_telemetry_pack(self) -> None:
        plan = Plan(
            mode="full",
            steps=[
                PlanStep(
                    module_name="chat-orchestrator",
                    args={
                        "context": {
                            "brain_turn_telemetry": {
                                "prompt_tokens_est": 3100,
                                "brain_recent_limit": 12,
                                "brain_profile": "standard",
                            }
                        }
                    },
                ),
            ],
        )
        tel = _brain_telemetry_from_plan(plan)
        self.assertEqual(tel.get("prompt_tokens_est"), 3100)
        self.assertEqual(tel.get("brain_recent_limit"), 12)

    def test_brain_recent_limit_in_turn_record(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "turns.jsonl"
            with patch.dict(os.environ, {"GEMMA_TURNS_LOG_PATH": str(path)}, clear=False):
                record_from_turn_outcome(
                    {
                        "user_id": "u1",
                        "outcome": "ok",
                        "profile": "standard",
                        "prompt_tokens_est": 900,
                        "brain_recent_limit": 10,
                    }
                )
            row = json.loads(path.read_text(encoding="utf-8").strip())
            self.assertEqual(row.get("brain_recent_limit"), 10)
            self.assertEqual(row.get("prompt_tokens_est"), 900)


if __name__ == "__main__":
    unittest.main()
