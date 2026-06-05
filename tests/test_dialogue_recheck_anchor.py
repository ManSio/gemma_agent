"""Тесты recheck-anchor и расширенного wall-clock."""
from __future__ import annotations

import unittest

from core.dialogue_recheck_anchor import (
    build_recheck_anchor_hint,
    last_substantive_user_question,
    looks_like_recheck_last_answer,
)
from core.incident_context_hint import build_incident_context_hint
from core.timezone_inference import (
    apply_stated_timezone_to_facts,
    format_wall_clock_user_reply,
    looks_like_wall_clock_question,
    parse_location_timezone_from_statement,
)


class RecheckAnchorTests(unittest.TestCase):
    def test_recheck_phrase(self):
        self.assertTrue(looks_like_recheck_last_answer("Может ты хорошо посмотришь?"))
        self.assertFalse(looks_like_recheck_last_answer("посмотри назад по переписке"))

    def test_recheck_too_short_or_long(self):
        self.assertFalse(looks_like_recheck_last_answer("да"))
        self.assertFalse(looks_like_recheck_last_answer("x" * 121))

    def test_recheck_english_phrases(self):
        self.assertTrue(looks_like_recheck_last_answer("look again please"))
        self.assertTrue(looks_like_recheck_last_answer("check again"))

    def test_not_recheck_normal_question(self):
        self.assertFalse(looks_like_recheck_last_answer("Сколько букв «Р» в слове Google?"))
        self.assertFalse(looks_like_recheck_last_answer("Найди всю информацию по инциденту"))

    def test_anchor_uses_last_question(self):
        rd = [
            {"role": "user", "text": "Про Галац и дрон"},
            {"role": "assistant", "text": "Длинный ответ про Галац"},
            {"role": "user", "text": "Сколько букв «Р» в слове Google?"},
            {"role": "assistant", "text": "Nоль."},
        ]
        hint = build_recheck_anchor_hint("Может ты хорошо посмотришь?", rd)
        self.assertIn("Google", hint)
        self.assertIn("Перепроверка", hint)
        self.assertNotIn("Галац", hint)

    def test_anchor_empty_history(self):
        hint = build_recheck_anchor_hint("Может ты хорошо посмотришь?", [])
        self.assertIn("Перепроверка", hint)
        self.assertNotIn("Приоритетный вопрос", hint)

    def test_anchor_not_recheck_phrase(self):
        rd = [{"role": "user", "text": "Сколько букв «Р» в слове Google?"}]
        self.assertEqual(
            build_recheck_anchor_hint("Сколько букв «Р» в слове Google?", rd),
            "",
        )

    def test_last_substantive_skips_recheck(self):
        rd = [
            {"role": "user", "text": "Сколько букв «Р» в слове Google?"},
            {"role": "assistant", "text": "Ноль."},
        ]
        q = last_substantive_user_question(rd, skip_current=False)
        self.assertIn("Google", q or "")


class WallClockExtendedTests(unittest.TestCase):
    def test_casual_time_phrases(self):
        self.assertTrue(looks_like_wall_clock_question("Сколько время?"))
        self.assertTrue(looks_like_wall_clock_question("Сколько у тебя сейчас время?"))
        self.assertTrue(looks_like_wall_clock_question("Какое у меня локальное время сейчас?"))
        self.assertFalse(looks_like_wall_clock_question("сколько букв в слове"))

    def test_piter_timezone_statement(self):
        parsed = parse_location_timezone_from_statement("у меня питерское время")
        self.assertEqual(parsed.get("timezone"), "Europe/Moscow")
        self.assertIn("Петербург", parsed.get("city", ""))

    def test_apply_stated_tz(self):
        facts: dict = {}
        self.assertTrue(apply_stated_timezone_to_facts("Я в Санкт-Петербурге", facts))
        self.assertEqual(facts.get("timezone"), "Europe/Moscow")

    def test_spb_clock_label(self):
        out = format_wall_clock_user_reply(
            effective_tz="Europe/Moscow",
            city="Санкт-Петербург",
        )
        self.assertIn("петербург", out.lower())


class IncidentHintTests(unittest.TestCase):
    def test_incident_from_recent(self):
        rd = [
            {"role": "user", "text": "В румынском Галаце беспилотник упал на жилой дом"},
            {"role": "assistant", "text": "Кратко по событию"},
        ]
        hint = build_incident_context_hint("Найди всю информацию по инциденту", rd)
        self.assertIn("Галац", hint)
        self.assertIn("приоритет", hint.lower())

    def test_incident_ops_deploy_subject(self):
        from core.incident_context_hint import extract_incident_subject_from_dialogue

        rd = [
            {
                "role": "user",
                "text": "Кратко: на сервере упал API после деплоя, логи в panel_nohup_bot.log",
            },
            {"role": "assistant", "text": "Принял"},
        ]
        sub = extract_incident_subject_from_dialogue(rd)
        self.assertIsNotNone(sub)
        assert sub is not None
        self.assertIn("panel_nohup", sub.lower())


if __name__ == "__main__":
    unittest.main()
