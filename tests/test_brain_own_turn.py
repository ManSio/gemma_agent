"""Реформа brain-own-turn: planner bypass по умолчанию выкл."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from core.brain_own_turn import (
    brain_own_turn_enabled,
    brain_pipeline_news_item_short_circuit_enabled,
    brain_pipeline_news_short_circuit_enabled,
    news_rss_fallback_enabled,
    pipeline_news_rss_fetch_enabled,
    planner_direct_allowed,
)
from core.brain_own_turn import brain_news_item_reply_enabled
from core.news_reply import news_direct_reply_enabled, news_item_pick_enabled
from core.weather_reply import weather_direct_reply_enabled


class BrainOwnTurnTests(unittest.TestCase):
    def test_defaults_block_planner_direct(self):
        env = {
            "BRAIN_OWN_TURN_ENABLED": "true",
            "BRAIN_OWN_TURN_ALLOW_NEWS": "false",
            "BRAIN_OWN_TURN_ALLOW_WEATHER": "false",
            "NEWS_RSS_FALLBACK_ENABLED": "false",
            "NEWS_DIGEST_SEARCH_ONLY": "false",
        }
        with patch.dict(os.environ, env, clear=False):
            self.assertTrue(brain_own_turn_enabled())
            self.assertFalse(planner_direct_allowed("news"))
            self.assertFalse(planner_direct_allowed("weather"))
            self.assertFalse(news_direct_reply_enabled())
            self.assertFalse(weather_direct_reply_enabled())
            self.assertFalse(news_rss_fallback_enabled())

    def test_legacy_allow_news_restores_direct(self):
        env = {
            "BRAIN_OWN_TURN_ENABLED": "true",
            "BRAIN_OWN_TURN_ALLOW_NEWS": "true",
            "NEWS_RSS_FALLBACK_ENABLED": "true",
            "NEWS_DIGEST_SEARCH_ONLY": "false",
        }
        with patch.dict(os.environ, env, clear=False):
            self.assertTrue(planner_direct_allowed("news"))
            self.assertTrue(news_direct_reply_enabled())
            self.assertTrue(news_rss_fallback_enabled())

    def test_news_item_brain_path_when_allow_off(self):
        with patch.dict(
            os.environ,
            {
                "BRAIN_OWN_TURN_ENABLED": "true",
                "BRAIN_OWN_TURN_ALLOW_NEWS_ITEM": "false",
                "BRAIN_NEWS_ITEM_REPLY_ENABLED": "true",
            },
            clear=False,
        ):
            self.assertTrue(brain_news_item_reply_enabled())
            self.assertTrue(news_item_pick_enabled())

    def test_news_item_off_when_brain_reply_disabled(self):
        with patch.dict(
            os.environ,
            {
                "BRAIN_OWN_TURN_ENABLED": "true",
                "BRAIN_OWN_TURN_ALLOW_NEWS_ITEM": "false",
                "BRAIN_NEWS_ITEM_REPLY_ENABLED": "false",
            },
            clear=False,
        ):
            self.assertFalse(news_item_pick_enabled())

    def test_pipeline_news_short_circuit_off_by_default(self):
        env = {
            "BRAIN_OWN_TURN_ENABLED": "true",
            "BRAIN_OWN_TURN_ALLOW_NEWS": "false",
            "NEWS_DIGEST_SEARCH_ONLY": "false",
        }
        with patch.dict(os.environ, env, clear=False):
            self.assertFalse(brain_pipeline_news_short_circuit_enabled())
            self.assertFalse(brain_pipeline_news_item_short_circuit_enabled())

    def test_pipeline_news_short_circuit_on_when_search_only(self):
        env = {
            "BRAIN_OWN_TURN_ENABLED": "true",
            "BRAIN_OWN_TURN_ALLOW_NEWS": "false",
            "NEWS_DIGEST_SEARCH_ONLY": "true",
        }
        with patch.dict(os.environ, env, clear=False):
            self.assertTrue(brain_pipeline_news_short_circuit_enabled())

    def test_pipeline_news_short_circuit_requires_allow(self):
        env = {
            "BRAIN_OWN_TURN_ENABLED": "true",
            "BRAIN_OWN_TURN_ALLOW_NEWS": "true",
            "BRAIN_PIPELINE_NEWS_SHORT_CIRCUIT": "true",
        }
        with patch.dict(os.environ, env, clear=False):
            self.assertTrue(brain_pipeline_news_short_circuit_enabled())

    def test_pipeline_rss_fetch_off_when_brain_owns_news(self):
        env = {
            "BRAIN_OWN_TURN_ENABLED": "true",
            "BRAIN_OWN_TURN_ALLOW_NEWS": "false",
            "NEWS_RSS_FALLBACK_ENABLED": "false",
            "NEWS_RESPECT_USER_SEARCH_OVER_RSS": "true",
        }
        with patch.dict(os.environ, env, clear=False):
            self.assertFalse(pipeline_news_rss_fetch_enabled("Какие новости в мире"))
            self.assertFalse(pipeline_news_rss_fetch_enabled("не rss — Беларусь"))

    def test_pipeline_rss_fetch_on_for_plain_news_when_legacy_allow(self):
        env = {
            "BRAIN_OWN_TURN_ENABLED": "true",
            "BRAIN_OWN_TURN_ALLOW_NEWS": "true",
            "NEWS_RSS_FALLBACK_ENABLED": "true",
            "NEWS_RESPECT_USER_SEARCH_OVER_RSS": "true",
            "NEWS_DIGEST_SEARCH_ONLY": "false",
        }
        with patch.dict(os.environ, env, clear=False):
            self.assertTrue(pipeline_news_rss_fetch_enabled("Какие новости"))
            self.assertFalse(
                pipeline_news_rss_fetch_enabled("последние новости не через rss")
            )


if __name__ == "__main__":
    unittest.main()
