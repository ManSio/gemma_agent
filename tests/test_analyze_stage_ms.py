"""Регрессия scripts/analyze_stage_ms.py."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import analyze_stage_ms as asm  # noqa: E402


class AnalyzeStageMsTests(unittest.TestCase):
    def test_report_pack_stage_and_slow(self) -> None:
        now = datetime.now(timezone.utc)
        ts = now.isoformat()
        turns = [
            {
                "ts": ts,
                "type": "turn",
                "latency_ms": 45000,
                "profile": "news_brief",
                "planner_reason": "intent_module_match",
                "user_excerpt": "какие новости",
                "stage_ms": {
                    "plan_start": 1,
                    "plan_context": 100,
                    "exec_modules_done": 42000,
                    "total": 45000,
                },
            },
            {
                "ts": ts,
                "type": "turn",
                "latency_ms": 800,
                "profile": "standard",
                "stage_ms": {"exec_modules_done": 600, "total": 800},
            },
        ]
        llm = [
            {"ts": ts, "telemetry_tag": "brain_first", "latency_ms": 7000.0}
            for _ in range(6)
        ]
        with tempfile.TemporaryDirectory() as td:
            tpath = Path(td) / "turns.jsonl"
            lpath = Path(td) / "llm.jsonl"
            tpath.write_text("\n".join(json.dumps(r) for r in turns), encoding="utf-8")
            lpath.write_text("\n".join(json.dumps(r) for r in llm), encoding="utf-8")
            cutoff = now - timedelta(days=1)
            report = asm._report_pack(
                turns_path=tpath,
                llm_path=lpath,
                cutoff=cutoff,
                days=1,
                slow_ms=30000,
                top_n=5,
            )
        self.assertEqual(report["turns"]["turns_with_latency"], 2)
        self.assertEqual(report["turns"]["turns_with_stage_ms"], 2)
        stages = {r["stage"]: r for r in report["turns"]["stage_ms"]}
        self.assertIn("exec_modules_done", stages)
        self.assertGreater(stages["exec_modules_done"]["median_ms"], 10000)
        self.assertEqual(report["turns"]["slow_turns"]["count"], 1)
        self.assertEqual(report["turns"]["slow_turns"]["profiles_top"][0][0], "news_brief")

    def test_render_contains_exec_modules(self) -> None:
        text = asm._render_text(
            {
                "window_days": 7,
                "turns_path": "/tmp/turns.jsonl",
                "llm_path": "/tmp/llm.jsonl",
                "turns": {
                    "turns_with_latency": 1,
                    "turns_in_window": 1,
                    "turns_with_stage_ms": 1,
                    "stage_ms_coverage_pct": 100.0,
                    "latency_ms": {"median": 1000, "p95": 2000, "max": 2000},
                    "stage_ms": [{"stage": "exec_modules_done", "n": 1, "median_ms": 900, "p95_ms": 900}],
                    "by_profile": [],
                    "slow_turns": {"threshold_ms": 30000, "count": 0, "profiles_top": [], "samples": []},
                },
                "llm_by_tag": [],
            }
        )
        self.assertIn("exec_modules_done", text)


if __name__ == "__main__":
    unittest.main()
