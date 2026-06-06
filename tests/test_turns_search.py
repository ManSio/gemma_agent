"""Поиск по turns_search.py."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.turns_search import _match_query, search_file


class TestTurnsSearch(unittest.TestCase):
    def test_match_query_all_tokens(self) -> None:
        self.assertTrue(_match_query("погода в минске сегодня", "погода минск"))
        self.assertFalse(_match_query("погода в минске", "погода район"))

    def test_search_file_respects_since(self) -> None:
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        new = datetime.now(timezone.utc).isoformat()
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "t.jsonl"
            p.write_text(
                json.dumps({"ts": old, "user_text": "погода завтра"}, ensure_ascii=False)
                + "\n"
                + json.dumps({"ts": new, "user_text": "погода сегодня"}, ensure_ascii=False)
                + "\n",
                encoding="utf-8",
            )
            since = datetime.now(timezone.utc) - timedelta(days=1)
            hits = search_file(
                p,
                "погода",
                since=since,
                fields=("user_text",),
                limit=10,
                skip_scenario=False,
                tail_lines=0,
            )
            self.assertEqual(len(hits), 1)
            self.assertIn("сегодня", hits[0]["user_text"])


if __name__ == "__main__":
    unittest.main()
