"""heuristic_misses.jsonl + router P1 guards."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.brain.profile_route_guard import preflight_profile
from core.brain.router_classifier import _guard_heuristic_profile_on_prose
from core.heuristic_context_gate import should_run_shortcut
from core.heuristic_misses_log import record_heuristic_miss


class HeuristicMissesAndP1Tests(unittest.TestCase):
    def test_misses_log_writes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "miss.jsonl"
            with patch.dict(
                os.environ,
                {
                    "HEURISTIC_MISSES_LOG_PATH": str(path),
                    "HEURISTIC_MISSES_LOG_ENABLED": "true",
                },
            ):
                record_heuristic_miss(
                    rule_id="geo_nearby",
                    verdict="blocked",
                    reason="prose_over_chars",
                    user_text="x " * 200,
                )
                self.assertTrue(path.is_file())
                row = json.loads(path.read_text(encoding="utf-8").strip())
                self.assertEqual(row["rule_id"], "geo_nearby")

    def test_embedded_habr_url_long_text_no_forced_summarization(self) -> None:
        text = ("обсуждение архитектуры " * 25) + "https://habr.com/ru/articles/999999/ "
        pre = preflight_profile(text)
        self.assertNotEqual(pre, "summarization")

    def test_prose_guard_downgrades_math_heuristic(self) -> None:
        story = (
            "день 1 баланс 1000 налог 13%. посчитай итог " + "подробно " * 30
        )
        out = _guard_heuristic_profile_on_prose("math_solve", story, {})
        self.assertEqual(out, "quick_explain")

    def test_geo_block_records_miss(self) -> None:
        from tests.test_heuristic_false_positives import DENTAL_RYADOM

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "miss.jsonl"
            with patch.dict(
                os.environ,
                {
                    "HEURISTIC_MISSES_LOG_PATH": str(path),
                    "HEURISTIC_MISSES_LOG_ENABLED": "true",
                },
            ):
                should_run_shortcut("geo_nearby", DENTAL_RYADOM)
                self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()
