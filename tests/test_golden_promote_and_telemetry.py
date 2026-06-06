"""E3/E4: golden promote + llm telemetry row normalization."""
from __future__ import annotations

import unittest

from core.golden_promote import chain_passes_for_golden, golden_record_from_report_row
from core.llm_telemetry import build_openrouter_telemetry, normalize_llm_usage_row, record_openrouter_completion


class GoldenPromoteTests(unittest.TestCase):
    def test_chain_passes_clean(self) -> None:
        row = {
            "pass": True,
            "chain": {
                "leaks": {"reply": []},
                "quality": {"issues": []},
                "after_execute": {"brain_profile": "standard"},
            },
        }
        self.assertTrue(chain_passes_for_golden(row))

    def test_chain_rejects_leak(self) -> None:
        row = {
            "pass": True,
            "chain": {
                "leaks": {"reply": [{"code": "instruction_leak"}]},
                "quality": {"issues": []},
            },
        }
        self.assertFalse(chain_passes_for_golden(row))

    def test_golden_record_shape(self) -> None:
        row = {
            "id": "reg_factorial",
            "user_text": "факториал",
            "reply_preview": "def factorial",
            "chain": {
                "leaks": {"reply": []},
                "quality": {"issues": []},
                "after_execute": {"brain_profile": "code_generation"},
                "llm_calls": 1,
            },
            "tags": ["code"],
        }
        rec = golden_record_from_report_row(row, ts="2026-05-24T00:00:00Z")
        self.assertEqual(rec["id"], "reg_factorial")
        self.assertEqual(rec["status"], "golden_verified")
        self.assertEqual(rec["profile"], "code_generation")


class LlmTelemetryRecordTests(unittest.TestCase):
    def test_build_openrouter_telemetry_defaults(self) -> None:
        t = build_openrouter_telemetry(tag=None)
        self.assertEqual(t["telemetry_tag"], "openrouter_chat")
        self.assertEqual(t["tag"], "openrouter_chat")
        self.assertTrue(t.get("telemetry_kind"))

    def test_record_always_has_tag(self) -> None:
        import os
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "usage.jsonl"
            os.environ["GEMMA_LLM_USAGE_PATH"] = str(log)
            summary = record_openrouter_completion(
                ok=True,
                requested_model="test/model",
                upstream_model="test/model",
                latency_ms=12.0,
                usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                telemetry={"kind": "chat"},
            )
            self.assertIsInstance(summary, dict)
            line = log.read_text(encoding="utf-8").strip()
            row = __import__("json").loads(line)
            self.assertEqual(row.get("telemetry_tag"), "openrouter_chat")

    def test_normalize_row_fills_telemetry_fields(self) -> None:
        row = normalize_llm_usage_row({"prompt_tokens": 10})
        self.assertEqual(row["telemetry_tag"], "openrouter_chat")
        self.assertEqual(row["kind"], row["telemetry_kind"])


if __name__ == "__main__":
    unittest.main()
