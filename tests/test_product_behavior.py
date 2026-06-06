"""Контракт продукта: pivot, search, reply gates."""
from __future__ import annotations

import unittest

from core.product_behavior import (
    apply_pivot_context_hygiene,
    assistant_reply_issues,
    build_cosmology_scope_hint,
    enrich_search_query,
    extract_search_query,
    should_force_product_search,
    price_or_commerce_search_required,
    subject_bucket,
    topic_pivot,
)


class TestProductBehavior(unittest.TestCase):
    def test_subject_bucket_commerce_vs_science(self):
        self.assertEqual(subject_bucket("Найди Samsung s26"), "commerce")
        self.assertEqual(subject_bucket("почему земля круглая"), "science")

    def test_topic_pivot_samsung_to_earth(self):
        self.assertTrue(
            topic_pivot(
                "почему земля круглая",
                "Найди мне все про Samsung s26",
            )
        )
        self.assertFalse(topic_pivot("ещё про s26", "Найди Samsung s26"))

    def test_should_force_search_rb_prices(self):
        self.assertTrue(should_force_product_search("посмотри цены в рб"))
        self.assertTrue(should_force_product_search("мог взять цены и магазины"))

    def test_science_chat_not_force_search(self):
        self.assertFalse(should_force_product_search("Вот смотри первая клетка как создать из химии"))
        self.assertFalse(should_force_product_search("Тогда пусть назовут первого создателя жизни"))
        self.assertFalse(price_or_commerce_search_required("как создать клетку из химии"))

    def test_social_advice_not_force_search(self):
        self.assertFalse(should_force_product_search("как найти друзей"))
        self.assertFalse(price_or_commerce_search_required("как найти друзей"))
        self.assertFalse(should_force_product_search("Что нового в мире"))

    def test_scenario_word_not_price_search(self):
        """«сценарию» содержит «цена» — не commerce search contract."""
        prose = (
            "посчитай итоговую оценку риска ликвидности по сценарию usd eur byn — "
            "таблица итераций"
        )
        self.assertFalse(should_force_product_search(prose))

    def test_enrich_query_country_generic(self):
        q = enrich_search_query("Samsung Galaxy S26", {"country": "Exampleland"})
        self.assertIn("Samsung", q)
        self.assertIn("Exampleland", q)

    def test_extract_search_query(self):
        self.assertIn("Samsung", extract_search_query("Найди мне все про Samsung s26"))

    def test_pivot_trims_recent(self):
        ctx = {
            "topic_tracking": {"current": "Найди Samsung s26", "snippet": "s26"},
            "recent_messages": [{"role": "user", "text": f"m{i}"} for i in range(10)],
            "user_id": "1",
        }
        out = apply_pivot_context_hygiene(ctx, "почему земля круглая", user_id="1")
        self.assertTrue(out.get("product_behavior_pivot"))
        n = len(out.get("recent_messages") or [])
        self.assertGreaterEqual(n, 4)
        self.assertLessEqual(n, 6)

    def test_reply_topic_drift(self):
        issues = assistant_reply_issues(
            "почему земля круглая",
            "В Беларуси цены на Samsung Galaxy S26 от 2900 руб",
            "",
        )
        self.assertIn("topic_drift", issues)

    def test_reply_echo(self):
        prev = "В Беларуси цены на Samsung Galaxy S26 от 2900 руб (МТС)."
        issues = assistant_reply_issues(
            "почему огонь горит",
            prev,
            prev,
        )
        self.assertIn("reply_echo", issues)

    def test_chitchat_skips_reply_echo(self):
        prev = "Привет, у меня всё отлично! Рад тебя слышать."
        issues = assistant_reply_issues(
            "привет как дела",
            prev,
            prev,
        )
        self.assertNotIn("reply_echo", issues)

    def test_cosmology_hint_on_clarify(self):
        recent = [{"role": "user", "text": "почему космос черный"}]
        h = build_cosmology_scope_hint("я про вселенную", recent)
        self.assertIn("Вселенной", h)
        self.assertIn("не", h.lower())

    def test_bot_scope_leak_on_universe_clarify(self):
        issues = assistant_reply_issues(
            "я про вселенную",
            "Вселенная моделирование ограничено специально для тестирования системы",
            recent_dialogue=[{"role": "user", "text": "почему космос черный"}],
        )
        self.assertIn("bot_scope_leak", issues)


if __name__ == "__main__":
    unittest.main()
