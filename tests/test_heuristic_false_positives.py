"""
Регрессия ложных срабатываний эвристик (geo, code, math, playbook).

Корпус — реальные инциденты VPS/deploy-host; при добавлении shortcut в plan() — новый кейс сюда.
"""
from __future__ import annotations

import unittest

from core.brain.code_empty_recovery import (
    code_reply_incomplete,
    looks_like_internal_code_monologue,
)
from core.geo_nearby_reply import (
    is_explicit_nearby_request,
    is_geo_topic_context,
    is_relational_ryadom,
    try_geo_nearby_reply_sync,
)
from core.intent_heuristics import prose_narrative_disfavors_calculator
from core.scenario_engine import TurnContext
from core.situation_playbook import match_situation


DENTAL_RYADOM = (
    "ситуация такая один зуб гнилой нужно удалять. рядом с ним зуб с хроническим "
    "пульпитом пролечили и поставили цементную пломбу. они находятся рядом с друг другом. "
    "Какой план лечения этих зубов?"
)


class HeuristicFalsePositiveTests(unittest.TestCase):
  # --- geo ---
    def test_dental_not_nearby_or_geo_topic(self):
        self.assertFalse(is_explicit_nearby_request(DENTAL_RYADOM))
        self.assertFalse(is_geo_topic_context(DENTAL_RYADOM))
        self.assertIsNone(try_geo_nearby_reply_sync(DENTAL_RYADOM))
        self.assertTrue(is_relational_ryadom(DENTAL_RYADOM))

    def test_dental_situation_lane_not_geo(self):
        ctx = TurnContext(user_text=DENTAL_RYADOM, intent="general")
        entry = match_situation(ctx)
        if entry:
            self.assertNotEqual(entry.lane, "standard", msg=f"wrong lane id={entry.id}")

    def test_real_geo_still_works(self):
        self.assertTrue(is_explicit_nearby_request("что рядом"))
        self.assertTrue(is_explicit_nearby_request("кафе рядом"))
        self.assertTrue(is_geo_topic_context("какая погода сейчас здесь"))

    def test_relational_ryadom_variants(self):
        for t in (
            "дома стоят рядом с друг другом",
            "рядом с ним лежит ключ",
            "зуб рядом с пломбой болит",
        ):
            self.assertTrue(is_relational_ryadom(t), msg=t)
            self.assertFalse(is_explicit_nearby_request(t), msg=t)

  # --- code ---
    def test_code_monologue_not_payload(self):
        leak = "Мы в режиме code_generation, пользователь просит функцию. Нужно дать код."
        self.assertTrue(looks_like_internal_code_monologue(leak))
        self.assertTrue(
            code_reply_incomplete("напиши функцию на Python для факториала", leak)
        )

    def test_code_intro_incomplete(self):
        self.assertTrue(
            code_reply_incomplete(
                "напиши функцию на Python для факториала",
                "Вот рекурсивная функция факториала на Python:",
            )
        )

  # --- math narrative ---
    def test_financial_story_not_forced_calc(self):
        story = (
            "день 1: баланс 1000, налог 13%. день 2: депозит 500, процент 5. "
            "итоговая оценка риска ликвидности по сценарию usd eur byn — таблица итераций "
            "формул и критерий статуса"
        )
        self.assertTrue(prose_narrative_disfavors_calculator(story))

    def test_short_math_question_not_narrative(self):
        self.assertFalse(prose_narrative_disfavors_calculator("сколько 2+2"))

    def test_financial_prose_not_math_profile(self):
        from core.brain.profile_registry import profile_from_text_heuristics

        story = (
            "день 1: баланс 1000, налог 13%. день 2: депозит 500, процент 5. "
            "посчитай итоговую оценку риска ликвидности по сценарию usd eur byn — таблица итераций "
            "формул и критерий статуса"
        )
        self.assertIsNone(profile_from_text_heuristics(story))

    def test_explicit_calc_still_math_profile(self):
        from core.brain.profile_registry import profile_from_text_heuristics

        self.assertEqual(profile_from_text_heuristics("посчитай 2+2"), "math_solve")

    def test_medical_error_not_code_debug(self):
        from core.brain.profile_registry import profile_from_text_heuristics

        text = (
            "после лечения пульпита осталась ошибка в прикусе, зуб болит. "
            "опиши план дальше " + "подробно " * 20
        )
        self.assertIsNone(profile_from_text_heuristics(text))

    def test_traceback_still_code_debug(self):
        from core.brain.profile_registry import profile_from_text_heuristics

        self.assertEqual(
            profile_from_text_heuristics("Traceback (most recent call last):\nValueError: x"),
            "code_debug",
        )

    def test_legal_article_in_prose_not_legal_profile(self):
        from core.brain.profile_registry import profile_from_text_heuristics

        article = (
            "В статье автор пишет что статья 5 закона не применяется к частным лицам. "
            + "Обсуждение " * 30
        )
        prof = profile_from_text_heuristics(article)
        self.assertNotEqual(prof, "legal")

    def test_pasted_news_not_news_brief_profile(self):
        from core.brain.profile_registry import profile_from_text_heuristics
        from tests.test_news_article_detection import HAVAL_PASTE

        prof = profile_from_text_heuristics(HAVAL_PASTE)
        self.assertNotEqual(prof, "news_brief")

    def test_headlines_still_news_brief_profile(self):
        from core.brain.profile_registry import profile_from_text_heuristics

        self.assertEqual(profile_from_text_heuristics("какие новости в мире"), "news_brief")

    def test_translation_prefix_at_start(self):
        from core.brain.profile_registry import profile_from_text_heuristics

        self.assertEqual(profile_from_text_heuristics("переведи на английский: привет"), "translation")

    def test_translation_mid_article_blocked(self):
        from core.brain.profile_registry import profile_from_text_heuristics

        text = ("контекст статьи " * 40) + "\nпереведи последний абзац"
        self.assertNotEqual(profile_from_text_heuristics(text), "translation")

    def test_explain_mid_article_not_quick_explain(self):
        from core.brain.profile_registry import profile_from_text_heuristics

        text = ("абзац статьи " * 45) + "\nобъясни последний абзац"
        self.assertNotEqual(profile_from_text_heuristics(text), "quick_explain")

    def test_short_explain_still_quick_explain(self):
        from core.brain.profile_registry import profile_from_text_heuristics

        self.assertEqual(profile_from_text_heuristics("объясни кратко что такое KV cache"), "quick_explain")

    def test_research_in_prose_not_research_profile(self):
        from core.brain.profile_registry import profile_from_text_heuristics

        text = ("контекст " * 50) + "исследуй вопрос в конце"
        self.assertNotEqual(profile_from_text_heuristics(text), "research")

    def test_short_research_still_research(self):
        from core.brain.profile_registry import profile_from_text_heuristics

        self.assertEqual(profile_from_text_heuristics("исследуй тему квантовых компьютеров"), "research")

    def test_bot_troubleshooting_still_works(self):
        from core.brain.profile_registry import profile_from_text_heuristics

        self.assertEqual(
            profile_from_text_heuristics("бот не работает после обновления"),
            "troubleshooting",
        )

    def test_summarize_short_still_summarization(self):
        from core.brain.profile_registry import profile_from_text_heuristics

        self.assertEqual(profile_from_text_heuristics("кратко перескажи этот текст"), "summarization")


if __name__ == "__main__":
    unittest.main()
