"""Dedup lesson drafts in turn_quality_loop."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


class TestTurnQualityLessonDedup(unittest.TestCase):
    def test_jsonl_recent_keys_dedupes_same_excerpt(self):
        from core.turn_quality_loop import _jsonl_recent_keys

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "lessons.jsonl"
            row = {
                "user_id": "1",
                "user_excerpt": "Samsung s26",
                "issues": ["price_hallucination"],
                "fp": "abc",
            }
            p.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            keys = _jsonl_recent_keys(p)
            self.assertIn(("1", "abc", "Samsung s26", ("price_hallucination",)), keys)


if __name__ == "__main__":
    unittest.main()
