"""ACC-11: честный отказ лучше уверенной выдумки (Habr eval 2026)."""
from __future__ import annotations

import unittest

from core.agent_test_validators import validate_reply
from core.brain.text_helpers import (
    is_bot_operational_diag_question,
    is_bot_operational_diag_reply,
    looks_like_news_access_refusal,
    operational_diag_reply,
)
from core.news_reply import apply_news_prefetch_fallback_if_needed


class Acc11HonestRefusalTests(unittest.TestCase):
    def test_news_llm_refusal_detected(self):
        t = "У меня нет доступа к актуальным новостям в реальном времени."
        self.assertTrue(looks_like_news_access_refusal(t))

    def test_news_digest_not_false_refusal(self):
        t = "1. Заголовок\nКратко.\n· Reuters"
        self.assertFalse(looks_like_news_access_refusal(t))

    def test_prefetch_replaces_false_news_refusal(self):
        body = "Reuters: Event — details."
        refusal = "У меня нет доступа к актуальным новостям в реальном времени."
        out = apply_news_prefetch_fallback_if_needed(
            refusal,
            search_body=body,
            user_query="новости в мире",
            task_facts={"is_news": True},
            brain_profile="news_brief",
        )
        self.assertNotIn("нет доступа", out.lower())
        self.assertIn("Reuters", out)

    def test_operational_diag_not_on_article_paste(self):
        paste = (
            "RAGAS и golden_dataset для оценки качества. "
            "UrlFetch и UniversalSearch в цепочке веб-фактов."
        )
        self.assertFalse(is_bot_operational_diag_question(paste))

    def test_operational_diag_on_real_admin_question(self):
        self.assertTrue(
            is_bot_operational_diag_question("openrouter не работает, ошибка 429, проверь ключ api")
        )

    def test_operational_diag_reply_is_honest_admin_template(self):
        self.assertTrue(is_bot_operational_diag_reply(operational_diag_reply()))

    def test_system_fallback_fails_probe_validator(self):
        case = {"validators": ["no_fallback"]}
        errs = validate_reply(
            "Не удалось сформировать нормальный ответ.",
            "любой вопрос",
            case,
        )
        self.assertIn("fallback_message", errs)


if __name__ == "__main__":
    unittest.main()
