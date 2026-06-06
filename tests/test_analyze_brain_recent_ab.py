"""Регрессия scripts/analyze_brain_recent_ab.py (C6)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import analyze_brain_recent_ab as ab  # noqa: E402


class AnalyzeBrainRecentAbTests(unittest.TestCase):
    def test_report_delta_10_vs_12_from_llm_usage(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        rows = [
            {"ts": now, "tag": "brain_first", "prompt_tokens": 1000, "brain_recent_limit": 10},
            {"ts": now, "tag": "brain_first", "prompt_tokens": 1100, "brain_recent_limit": 10},
            {"ts": now, "tag": "brain_first", "prompt_tokens": 1200, "brain_recent_limit": 12},
            {"ts": now, "tag": "brain_first", "prompt_tokens": 1300, "brain_recent_limit": 12},
        ]
        with tempfile.TemporaryDirectory() as td:
            llm = Path(td) / "llm.jsonl"
            turns = Path(td) / "turns.jsonl"
            llm.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            turns.write_text("", encoding="utf-8")
            cutoff = datetime.now(timezone.utc) - ab.timedelta(days=1)
            report = ab._report_pack(
                llm_path=llm,
                turns_path=turns,
                cutoff=cutoff,
                days=1,
                profile_filter="",
            )
        lim = report["llm_usage"]["by_recent_limit"]
        self.assertEqual(lim["10"]["p50"], 1050)
        self.assertEqual(lim["12"]["p50"], 1250)
        self.assertEqual(report["llm_usage"]["delta_p50_12_minus_10"], 200)

    def test_analyze_turns_groups_by_recent_limit(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        rows = [
            {"ts": now, "prompt_tokens_est": 500, "brain_recent_limit": 10},
            {"ts": now, "prompt_tokens_est": 700, "brain_recent_limit": 12},
        ]
        with tempfile.TemporaryDirectory() as td:
            turns = Path(td) / "turns.jsonl"
            turns.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            cutoff = datetime.now(timezone.utc) - ab.timedelta(days=1)
            tpack = ab._analyze_turns(turns, cutoff=cutoff, profile_filter="")
        self.assertEqual(tpack["n"], 2)
        self.assertEqual(ab._p50(tpack["by_recent"][10]), 500)
        self.assertEqual(ab._p50(tpack["by_recent"][12]), 700)


if __name__ == "__main__":
    unittest.main()
