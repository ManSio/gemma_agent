"""Регрессия: сырой TOOL_CALL не должен уходить пользователю."""
from __future__ import annotations

import unittest

from core.brain.response_finalize import finalize_user_reply
from core.brain.text_helpers import looks_like_leaked_tool_call_leak, strip_leaked_tool_call_markup
from core.text_leak_scan import outbound_has_blocking_leak


class TestToolCallLeakGuard(unittest.TestCase):
    def test_strip_tool_call_only(self) -> None:
        raw = 'TOOL_CALL:\n{"name": "UrlFetch.fetch_page", "args": {"url": "https://habr.com/x"}}'
        self.assertFalse(strip_leaked_tool_call_markup(raw).strip())
        self.assertTrue(looks_like_leaked_tool_call_leak(raw))

    def test_finalize_user_reply_empty_on_tool_call(self) -> None:
        raw = 'TOOL_CALL:\n{"name": "Search.web", "args": {"query": "test"}}'
        self.assertEqual(finalize_user_reply(raw, user_text="x"), "")

    def test_text_leak_scan_blocks_tool_call_marker(self) -> None:
        raw = "TOOL_CALL: {\"name\": \"x\", \"args\": {}}"
        self.assertTrue(outbound_has_blocking_leak(raw))


if __name__ == "__main__":
    unittest.main()
