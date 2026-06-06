"""Фоновый аудит ходов."""
from __future__ import annotations

import unittest

from core.turn_quality_loop import audit_turn_payload


class TestTurnQualityLoop(unittest.TestCase):
    def test_detects_topic_drift_science_after_commerce(self):
        audit = audit_turn_payload(
            {
                "user_excerpt": "почему земля круглая",
                "assistant_excerpt": "В Беларуси цены на Samsung Galaxy S26 от 2900",
                "user_id": "1",
                "outcome": "ok",
                "intent": "explain",
                "module": "chat_orchestrator",
            }
        )
        self.assertIn("topic_drift", audit.get("issues") or [])

    def test_detects_wrong_clarify_on_prices(self):
        audit = audit_turn_payload(
            {
                "user_excerpt": "посмотри цены в рб",
                "assistant_excerpt": "Чем ещё могу помочь?",
                "user_id": "1",
                "outcome": "clarify",
                "intent": "general",
                "module": "chat_orchestrator",
            }
        )
        self.assertIn("wrong_route_clarify", audit.get("issues") or [])

    def test_clean_turn_no_issues(self):
        audit = audit_turn_payload(
            {
                "user_excerpt": "привет",
                "assistant_excerpt": "Здравствуйте!",
                "user_id": "1",
                "outcome": "ok",
                "intent": "general",
                "module": "chat_orchestrator",
            }
        )
        self.assertEqual(audit.get("issues") or [], [])

    def test_friends_no_search_skipped(self):
        audit = audit_turn_payload(
            {
                "user_excerpt": "как найти друзей",
                "assistant_excerpt": "Попробуй клубы по интересам и волонтёрство.",
                "user_id": "1",
                "outcome": "ok",
                "intent": "general",
                "module": "chat_orchestrator",
            }
        )
        self.assertNotIn("search_skipped", audit.get("issues") or [])

    def test_news_turn_no_search_skipped(self):
        audit = audit_turn_payload(
            {
                "user_excerpt": "Что нового в мире",
                "assistant_excerpt": "1. Заголовок новости",
                "user_id": "1",
                "outcome": "ok",
                "intent": "news",
                "module": "chat_orchestrator",
                "scenario_hits": [{"id": "news_turn", "action": "prefer_news_direct"}],
            }
        )
        self.assertNotIn("search_skipped", audit.get("issues") or [])

    def test_skip_probe_uid(self):
        from core.turn_quality_loop import _should_skip_quality_loop

        self.assertTrue(_should_skip_quality_loop({"user_id": "probe_smoke"}))
        self.assertFalse(_should_skip_quality_loop({"user_id": "u_real_1"}))


if __name__ == "__main__":
    unittest.main()
