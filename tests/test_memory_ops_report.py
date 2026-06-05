"""memory_ops_report smoke + turn_quality heuristic miss hook."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class MemoryOpsReportTests(unittest.TestCase):
    def test_build_report_empty_turns(self) -> None:
        from core.memory_ops_report import build_memory_ops_report

        with patch("core.turn_observer.read_recent_turns", return_value=[]):
            with patch(
                "core.memory_runtime_report.build_memory_insight_payload",
                return_value={
                    "generated_at": "2026-01-01T00:00:00+00:00",
                    "limits": {"entries_per_file": 5},
                    "flags": {},
                    "paths": {},
                    "legends": {},
                    "strategy_paths_tail": [],
                    "route_risk_tail": [],
                    "experience_tail": [],
                    "behavior_session": None,
                },
            ):
                with patch(
                    "core.memory_runtime_report.format_memory_insight_plain",
                    return_value="stub memory",
                ):
                    out = build_memory_ops_report(turns_limit=5, memory_limit=2, misses_tail=10)
        self.assertIn("memory_ops_report", out)
        self.assertIn("turns.jsonl", out)
        self.assertIn("heuristic_misses", out)

    def test_shortcut_rule_id_from_payload(self) -> None:
        from core.memory_ops_report import shortcut_rule_id_from_turn_payload

        self.assertEqual(
            shortcut_rule_id_from_turn_payload({"shortcut_rule_id": "geo_city"}),
            "geo_city",
        )
        self.assertEqual(
            shortcut_rule_id_from_turn_payload(
                {"router_route_audit": {"heuristic_gate": [{"shortcut_rule_id": "math_fact"}]}}
            ),
            "math_fact",
        )
        self.assertEqual(shortcut_rule_id_from_turn_payload({}), "")


class TurnQualityHeuristicMissTests(unittest.TestCase):
    def test_logs_miss_when_shortcut_and_issues(self) -> None:
        from core.turn_quality_loop import _log_heuristic_miss_on_bad_shortcut

        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "data" / "runtime" / "heuristic_misses.jsonl"
            with patch.dict(
                "os.environ",
                {
                    "GEMMA_PROJECT_ROOT": td,
                    "HEURISTIC_MISSES_LOG_ENABLED": "true",
                    "HEURISTIC_MISSES_LOG_PATH": "",
                },
            ):
                ok = _log_heuristic_miss_on_bad_shortcut(
                    {
                        "shortcut_rule_id": "geo_city",
                        "user_excerpt": "где стоматология",
                        "user_id": "u1",
                        "topic_current": "geo",
                    },
                    ["wrong_route"],
                )
                self.assertTrue(ok)
                self.assertTrue(log_path.is_file())
                body = log_path.read_text(encoding="utf-8")
                self.assertIn("geo_city", body)
                self.assertIn("quality_loop", body)

    def test_skips_without_rule_id(self) -> None:
        from core.turn_quality_loop import _log_heuristic_miss_on_bad_shortcut

        self.assertFalse(_log_heuristic_miss_on_bad_shortcut({"user_excerpt": "hi"}, ["empty_reply"]))


if __name__ == "__main__":
    unittest.main()
