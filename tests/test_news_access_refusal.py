"""Регрессия: LLM-отказ «нет доступа к новостям» при готовой prefetch-сводке."""
from __future__ import annotations

import unittest

from core.brain.text_helpers import looks_like_news_access_refusal
from core.brain_own_turn import (
    pipeline_news_emergency_rss_on_search_fail_enabled,
    pipeline_news_rss_fetch_enabled,
)
from core.news_reply import apply_news_prefetch_fallback_if_needed


class NewsAccessRefusalTests(unittest.TestCase):
    def test_detects_russian_refusal(self):
        t = (
            "Извините, но у меня нет доступа к актуальным новостям в реальном времени. "
            "Мой предыдущий ответ остаётся в силе."
        )
        self.assertTrue(looks_like_news_access_refusal(t))

    def test_normal_digest_not_refusal(self):
        t = "1. Заголовок\nКратко о событии.\n· Reuters"
        self.assertFalse(looks_like_news_access_refusal(t))

    def test_prefetch_fallback_replaces_refusal(self):
        body = "Reuters: Event A — details.\nBBC: Event B — more."
        refusal = "У меня нет доступа к актуальным новостям в реальном времени."
        out = apply_news_prefetch_fallback_if_needed(
            refusal,
            search_body=body,
            user_query="какие новости в мире",
            task_facts={"is_news": True},
            brain_profile="news_brief",
        )
        self.assertNotIn("нет доступа", out.lower())
        self.assertIn("Reuters", out)

    def test_prefetch_fallback_keeps_good_reply(self):
        good = "1. Тест\nАбзац новости.\n· Источник"
        body = "hidden prefetch"
        out = apply_news_prefetch_fallback_if_needed(
            good,
            search_body=body,
            user_query="новости",
            task_facts={"is_news": True},
        )
        self.assertEqual(good, out)

    def test_emergency_rss_default_on_while_planner_rss_off(self):
        import os
        from unittest.mock import patch

        env = {
            "BRAIN_OWN_TURN_ENABLED": "true",
            "BRAIN_OWN_TURN_ALLOW_NEWS": "false",
            "NEWS_RSS_FALLBACK_ENABLED": "false",
            "NEWS_DIGEST_SEARCH_ONLY": "false",
            "NEWS_PIPELINE_RSS_ON_SEARCH_FAIL": "true",
        }
        with patch.dict(os.environ, env, clear=False):
            self.assertFalse(pipeline_news_rss_fetch_enabled("Какие новости в мире"))
            self.assertTrue(pipeline_news_emergency_rss_on_search_fail_enabled())


if __name__ == "__main__":
    unittest.main()
