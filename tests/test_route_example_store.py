"""route_examples.jsonl — append и corpus mapping."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.route_example_store import append_route_example, route_example_to_corpus_case


class TestRouteExampleStore(unittest.TestCase):
    def test_append_and_corpus_case(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "route_examples.jsonl"
            with mock.patch.dict(
                os.environ,
                {"ROUTE_EXAMPLES_PATH": str(path)},
                clear=False,
            ):
                rec = append_route_example(
                    text="Какие новости в Европе",
                    expected_profile="news_brief",
                    added_by="test",
                )
                self.assertEqual(rec["expected_profile"], "news_brief")
                lines = path.read_text(encoding="utf-8").strip().splitlines()
                self.assertEqual(len(lines), 1)
                row = json.loads(lines[0])
                case = route_example_to_corpus_case(row)
                self.assertTrue(case.get("route_only"))
                self.assertEqual(case["expect_preflight_profile"], "news_brief")


if __name__ == "__main__":
    unittest.main()
