"""Утечки схем инструментов и внутреннего JSON в ответ."""
from __future__ import annotations

import unittest

from core.brain.schema_leak_strip import looks_like_tool_schema_leak, strip_tool_schema_leak


SCHEDULE_LEAK = (
    '"Выдаёт ссылки на файлы XLSX или XLS с расписаниями пригородных поездов Беларуси",'
    '"parameters":{"type":"object","properties":{"direction":{"type":"string","enum":'
    '["Минск - Барановичи","Минск - Бобруйск"]}},"required":["direction"]}} response: str'
)

CTX_LEAK = (
    "': {'verbosity': 'concise', 'tone': 'balanced'}, 'micro_emotion_style': {'optimism': 0.8}, "
    "'user_active_context': {'intent': 'how_to_synthesize_cell'}}"
)


class SchemaLeakStripTests(unittest.TestCase):
    def test_detects_schedule_schema(self) -> None:
        self.assertTrue(looks_like_tool_schema_leak(SCHEDULE_LEAK))

    def test_detects_internal_context_blob(self) -> None:
        self.assertTrue(looks_like_tool_schema_leak(CTX_LEAK))

    def test_normal_code_not_leak(self) -> None:
        code = "```python\ndef add(a, b):\n    return a + b\n```"
        self.assertFalse(looks_like_tool_schema_leak(code))

    def test_strip_returns_empty_on_pure_leak(self) -> None:
        self.assertEqual(strip_tool_schema_leak(SCHEDULE_LEAK), "")

    def test_code_request_pattern_calculator(self) -> None:
        from core.brain.code_empty_recovery import user_requests_code

        self.assertTrue(user_requests_code("Напиши на питоне калькулятор"))

    def test_detects_admin_tools_list_leak(self) -> None:
        leak = '- tools: [{"name": "Admin.CheckDatabase", "description": "..."}]'
        self.assertTrue(looks_like_tool_schema_leak(leak))
        self.assertEqual(strip_tool_schema_leak(leak), "")


if __name__ == "__main__":
    unittest.main()
