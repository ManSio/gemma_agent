"""Unit-тесты core/admin_ops_metrics.py (без mutmut/LLM)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from core.admin_ops_metrics import (
    collect_admin_self_metrics,
    format_ms,
    format_recent_ab_counts,
    summarize_llm_usage_window,
    summarize_turns_window,
)


def _ts(hours_ago: float = 0.0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return dt.isoformat()


class AdminOpsMetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        (self.root / "data" / "runtime").mkdir(parents=True)
        self._env = patch.dict(
            os.environ,
            {"GEMMA_LLM_USAGE_PATH": "", "GEMMA_TURNS_LOG_PATH": "", "GEMMA_PROJECT_ROOT": str(self.root)},
            clear=False,
        )
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmpdir.cleanup()

    def test_summarize_llm_kv_and_latency(self) -> None:
        p = self.root / "data/runtime/llm_usage.jsonl"
        rows = [
            {
                "ts": _ts(1),
                "ok": True,
                "telemetry_tag": "brain:standard",
                "telemetry_kind": "brain",
                "latency_ms": 2000,
                "cached_prompt_tokens": 100,
                "prompt_tokens": 200,
                "brain_recent_limit": 10,
            },
            {
                "ts": _ts(2),
                "ok": True,
                "telemetry_tag": "brain:standard",
                "telemetry_kind": "brain",
                "latency_ms": 4000,
                "cached_prompt_tokens": 0,
                "prompt_tokens": 300,
                "brain_recent_limit": 12,
            },
            {
                "ts": _ts(1),
                "ok": False,
                "telemetry_tag": "router",
                "latency_ms": 500,
            },
        ]
        p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

        s = summarize_llm_usage_window(self.root, hours=24)
        self.assertTrue(s["available"])
        self.assertEqual(s["brain_rows"], 2)
        self.assertEqual(s["llm_fail"], 1)
        self.assertEqual(s["kv_hit_pct"], 50.0)
        self.assertEqual(s["brain_latency_p50_ms"], 3000)
        self.assertEqual(s["recent_brain_counts"], {"10": 1, "12": 1})

    def test_summarize_turns_latency(self) -> None:
        p = self.root / "data/runtime/turns.jsonl"
        rows = [
            {"ts": _ts(1), "latency_ms": 1000, "issues": []},
            {"ts": _ts(2), "latency_ms": 3000, "issues": ["empty_reply"]},
        ]
        p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

        s = summarize_turns_window(self.root, hours=24)
        self.assertEqual(s["turns"], 2)
        self.assertEqual(s["issues"], 1)
        self.assertEqual(s["latency_p50_ms"], 2000)

    def test_format_helpers(self) -> None:
        self.assertEqual(format_ms(450), "450ms")
        self.assertEqual(format_ms(1800), "1.8s")
        self.assertEqual(format_ms(None), "—")
        self.assertIn("r10=2", format_recent_ab_counts({"10": 2, "12": 1}))

    def test_collect_bundle(self) -> None:
        p = self.root / "data/runtime/llm_usage.jsonl"
        p.write_text(
            json.dumps(
                {
                    "ts": _ts(0),
                    "ok": True,
                    "telemetry_kind": "brain",
                    "latency_ms": 100,
                    "prompt_tokens": 50,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        bundle = collect_admin_self_metrics(self.root, hours=24)
        self.assertIn("llm_24h", bundle)
        self.assertIn("turns_24h", bundle)
        self.assertIn("live", bundle)


if __name__ == "__main__":
    unittest.main()
