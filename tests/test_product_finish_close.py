import os
import unittest
from unittest.mock import patch

from core.brain.profile_registry import get_profile
from core.brain.text_helpers import wants_expanded_news_digest


class ProductFinishCloseTests(unittest.TestCase):
    def test_wants_expanded_news(self):
        self.assertTrue(wants_expanded_news_digest("новости развёрнуто"))
        self.assertTrue(wants_expanded_news_digest("что с новостями подробнее"))
        self.assertFalse(wants_expanded_news_digest("что нового в мире"))

    def test_wants_expanded_after_brief_digest(self):
        recent = [
            {"role": "assistant", "text": "Новости (мир):\n\n1. Заголовок A (reuters.com)\n\n2. Заголовок B"},
            {"role": "user", "text": "подробнее"},
        ]
        self.assertTrue(wants_expanded_news_digest("подробнее", recent))

    def test_standard_recent_count_env(self):
        with patch.dict(os.environ, {"BRAIN_STANDARD_RECENT_COUNT": "10"}, clear=False):
            self.assertEqual(get_profile("standard").recent_count, 10)

    def test_admin_self_html(self):
        from core.admin_self_status import build_admin_self_html

        html = build_admin_self_html()
        self.assertIn("self-status", html)
        self.assertIn("BRAIN_DIRECT_DIALOG", html)
        self.assertIn("Метрики", html)
        self.assertIn("PRE_LLM_PLAN_ENABLED", html)


if __name__ == "__main__":
    unittest.main()
