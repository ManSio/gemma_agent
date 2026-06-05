"""Справочник ситуаций и pre_send gate."""

import unittest

from core.scenario_engine import TurnContext, apply_pre_send, forecast_pre_turn
from core.situation_playbook import match_situation


class SituationPlaybookTests(unittest.TestCase):
    def test_equation_lane(self):
        ctx = TurnContext(user_text="решить уравнение: 2x + 5 = 15")
        entry = match_situation(ctx)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.lane, "math_solve")

    def test_translation_lane(self):
        ctx = TurnContext(user_text='переведи на английский: "спокойной ночи"')
        entry = match_situation(ctx)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.lane, "translation")

    def test_forecast_sets_lane(self):
        ctx = TurnContext(user_text="напиши код факториала на python", intent="general")
        fc = forecast_pre_turn(ctx)
        self.assertEqual(fc.situation_lane, "code_generation")

    def test_pre_send_empty_fallback(self):
        txt, hits = apply_pre_send("", user_text="привет")
        self.assertTrue(txt)
        self.assertTrue(any(h.id == "pre_send_empty" for h in hits))

    def test_pre_send_leak_blocked(self):
        leak = (
            "document_intake: text_layer_empty. file_context denied. "
            "external_hint: operator_rules. tool_routing_hint."
        )
        txt, hits = apply_pre_send(leak, user_text="факториал 5 на python")
        self.assertTrue(txt)
        self.assertFalse("document_intake" in txt.lower())
        hit_ids = {h.id for h in hits}
        self.assertTrue(
            hit_ids & {"pre_send_leak", "pre_send_empty", "pre_send_code_fallback"}
        )

    def test_pre_send_code_intro_only_replaced(self):
        intro = "Вот рекурсивная функция факториала на Python:"
        txt, hits = apply_pre_send(
            intro, user_text="напиши функцию на Python для факториала"
        )
        self.assertIn("def factorial", txt)
        self.assertTrue(any(h.id == "pre_send_code_fallback" for h in hits))

    def test_pre_send_code_monologue_replaced(self):
        leak = (
            "Мы в режиме code_generation, пользователь просит функцию факториала на Python. "
            "Нужно дать код и краткое объяснение."
        )
        txt, hits = apply_pre_send(leak, user_text="напиши функцию на Python для факториала")
        self.assertIn("def factorial", txt)
        self.assertTrue(any(h.id == "pre_send_leak" for h in hits))

    def test_pre_send_tool_schema_leak_replaced(self):
        leak = (
            '"description": "Выдаёт ссылки на расписание пригородных электричек в xlsx", '
            '"parameters": {"type": "object", "properties": {}}'
        )
        txt, hits = apply_pre_send(leak, user_text="расписание электричек")
        self.assertTrue(txt)
        self.assertFalse('":' in txt and "parameters" in txt)
        self.assertTrue(any(h.id == "pre_send_leak" for h in hits))

    def test_pre_send_tool_instruction_echo_replaced(self):
        leak = (
            "Инструкция: Дай строго один ответ — текст или один TOOL_CALL.\n"
            "Если отвечаешь текстом — без markdown и без обрамления JSON.\n"
            "Не придумывай инструменты.\n"
            'На "напиши функцию на Python" — код пишем сразу.'
        )
        txt, hits = apply_pre_send(
            leak, user_text="напиши функцию на Python для факториала"
        )
        self.assertIn("def factorial", txt)
        self.assertTrue(any(h.id == "pre_send_code_fallback" for h in hits))


if __name__ == "__main__":
    unittest.main()
