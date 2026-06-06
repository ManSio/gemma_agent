"""Пересланная статья ≠ запрос дайджеста новостей."""

import unittest

from core.brain.text_helpers import (
    looks_like_news_headlines_request,
    looks_like_pasted_news_article,
    task_fact_profile,
)
from core.brain.profile_registry import profile_from_text_heuristics
from core.telegram_output_guard import format_news_from_search


HAVAL_PASTE = """От 61 900 рублей: в Беларусь приехали обновленные HAVAL. Разбираемся, что предлагают покупателям

Еще недавно выбор автомобилей HAVAL в Беларуси был заметно скромнее, чем привыкли покупатели. На фоне роста цен, изменений в поставках и новых требований к локализации часть китайских моделей российской сборки стала менее доступной для белорусского рынка.

Теперь ситуация начала меняться: в страну поступили первые партии обновленных HAVAL после рестайлинга, а дистрибьютор говорит о высоком спросе почти на весь модельный ряд.

#myfin_news"""


class NewsArticleDetectionTests(unittest.TestCase):
    def test_hashtag_myfin_not_headlines_request(self):
        self.assertFalse(looks_like_news_headlines_request(HAVAL_PASTE))
        self.assertTrue(looks_like_pasted_news_article(HAVAL_PASTE))

    def test_explicit_headlines_request(self):
        self.assertTrue(looks_like_news_headlines_request("Какие новости в мире"))
        self.assertTrue(looks_like_news_headlines_request("последние новости Беларуси"))

    def test_task_facts_pasted_article(self):
        tf = task_fact_profile(HAVAL_PASTE, {})
        self.assertTrue(tf["is_pasted_article"])
        self.assertFalse(tf["is_news"])

    def test_profile_summarization_not_news_brief(self):
        prof = profile_from_text_heuristics(HAVAL_PASTE)
        self.assertIn(prof, ("summarization", "research"))
        self.assertNotEqual(prof, "news_brief")

    def test_news_digest_ends_with_period(self):
        raw = (
            "Лукашенко обсудил налоговую реформу на совещании: "
            "президент перечислил ключевые меры поддержки бизнеса - БелТА"
        )
        out = format_news_from_search(raw, user_query="новости")
        self.assertTrue(out.rstrip().endswith("."))


if __name__ == "__main__":
    unittest.main()
