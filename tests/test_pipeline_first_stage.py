"""Регрессия: pipeline_first_stage и pipeline_tool_exec."""
from __future__ import annotations

import unittest
from unittest import mock

from core.brain.pipeline_first_stage import (
    max_tool_call_retries,
    resolve_tool_calls_from_first_content,
)
from core.brain.pipeline_tool_exec import enrich_tool_args
from core.brain.text_helpers import parse_tool_call


class PipelineFirstStageTests(unittest.TestCase):
    def test_max_tool_call_retries_bounds(self) -> None:
        with mock.patch.dict("os.environ", {"BRAIN_TOOL_CALL_RETRY": "9"}):
            self.assertEqual(max_tool_call_retries(), 3)
        with mock.patch.dict("os.environ", {"BRAIN_TOOL_CALL_RETRY": "abc"}):
            self.assertEqual(max_tool_call_retries(), 1)

    def test_resolve_tool_calls_plain_text(self) -> None:
        tc, batched = resolve_tool_calls_from_first_content("просто ответ")
        self.assertFalse(tc and tc.get("name"))
        self.assertEqual(batched, [])

    def test_resolve_tool_calls_single(self) -> None:
        body = 'TOOL_CALL: {"name": "UrlFetch.fetch_page", "args": {"url": "https://example.com"}}'
        tc, batched = resolve_tool_calls_from_first_content(body)
        self.assertIsNotNone(tc)
        self.assertEqual(tc.get("name"), "UrlFetch.fetch_page")
        self.assertEqual(batched, [])


class PipelineToolExecTests(unittest.TestCase):
    def test_enrich_injects_user_id(self) -> None:
        name, args = enrich_tool_args(
            {"name": "Test.tool", "args": {}},
            user_id="u99",
            context={},
            user_facts={},
            task_facts={},
        )
        self.assertEqual(name, "Test.tool")
        self.assertEqual(args.get("user_id"), "u99")

    def test_parse_matches_resolve(self) -> None:
        body = 'TOOL_CALL: {"name": "UrlFetch.fetch_page", "args": {"url": "https://x.test"}}'
        tc, _ = resolve_tool_calls_from_first_content(body)
        direct = parse_tool_call(body)
        self.assertEqual(tc, direct)


if __name__ == "__main__":
    unittest.main()
