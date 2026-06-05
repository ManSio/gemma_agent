"""Регрессия scripts/analyze_module_hits.py."""
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

import analyze_module_hits as amh  # noqa: E402


class AnalyzeModuleHitsTests(unittest.TestCase):
    def test_report_counts_modules_and_profiles(self) -> None:
        now = datetime.now(timezone.utc)
        ts = now.isoformat()
        turns = [
            {
                "ts": ts,
                "module": "memory",
                "profile": "news_brief",
                "planner_reason": "wall_clock_direct",
                "last_tool": "UniversalSearch",
            },
            {
                "ts": ts,
                "module": "",
                "profile": "standard",
                "shortcut_rule_id": "news_direct",
            },
        ]
        with tempfile.TemporaryDirectory() as td:
            tpath = Path(td) / "turns.jsonl"
            tpath.write_text("\n".join(json.dumps(r) for r in turns), encoding="utf-8")
            cutoff = now - timedelta(days=1)
            report = amh._report_pack(tpath, cutoff=cutoff, days=1)
        mods = dict(report.get("modules_top") or [])
        self.assertEqual(mods.get("memory"), 1)
        profs = dict(report.get("profiles_top") or [])
        self.assertEqual(profs.get("news_brief"), 1)
        self.assertEqual(profs.get("standard"), 1)


if __name__ == "__main__":
    unittest.main()
