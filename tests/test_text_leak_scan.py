"""text_leak_scan."""
from __future__ import annotations

import unittest

from core.text_leak_scan import (
    has_blocking_leak,
    outbound_has_blocking_leak,
    primary_blocking_leak_code,
    scan_text_leaks,
)


class TestTextLeakScan(unittest.TestCase):
    def test_detects_xml_leak(self):
        t = "Ответ: <rule name='x'> запрещено"
        self.assertTrue(has_blocking_leak(t))

    def test_clean_reply_ok(self):
        self.assertFalse(has_blocking_leak("Привет! Чем помочь?"))

    def test_detects_tool_markup(self):
        leaks = scan_text_leaks("ArithmeticTool.evaluate failed", role="assistant")
        self.assertTrue(any(x["code"] == "prompt_markup_leak" for x in leaks))

    def test_internal_code_monologue_blocking(self):
        mono = (
            "Мы в режиме code_generation, пользователь просит функцию. "
            "Нужно дать код и краткое объяснение."
        )
        code = primary_blocking_leak_code(mono)
        self.assertIn(code, ("internal_code_monologue", "instruction_leak"))
        self.assertTrue(outbound_has_blocking_leak(mono))

    def test_pre_send_schema_leak_blocking(self):
        leak = (
            '"description": "Выдаёт ссылки на расписание пригородных электричек xlsx", '
            '"parameters": {"type": "object"}'
        )
        self.assertTrue(outbound_has_blocking_leak(leak))


if __name__ == "__main__":
    unittest.main()
