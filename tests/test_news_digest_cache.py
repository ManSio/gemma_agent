import os
import unittest
from unittest.mock import patch

from core.news_digest_cache import (
    cache_key,
    get_cached_compose,
    items_fingerprint,
    put_cached_compose,
)


class NewsDigestCacheTests(unittest.TestCase):
    def setUp(self):
        import core.news_digest_cache as mod

        with mod._lock:
            mod._store.clear()

    def test_same_headlines_hit(self):
        items = [
            {"title": "Иран и США: переговоры в тупике"},
            {"title": "Ракетный удар по Израилю"},
        ]
        fp = items_fingerprint(items)
        key = cache_key(user_query="какие новости в мире", world_feed=True)
        body = "Абзац один: переговоры зашли в тупик, стороны не сближают позиции.\n\n" * 3
        put_cached_compose(key, fp, body)
        hit = get_cached_compose(key, fp)
        self.assertIn("переговоры", hit or "")

    def test_new_headline_miss(self):
        items_a = [{"title": "Старость заголовка A"}]
        items_b = [{"title": "Совсем другая новость B"}]
        fp_a = items_fingerprint(items_a)
        fp_b = items_fingerprint(items_b)
        key = cache_key(user_query="новости")
        put_cached_compose(key, fp_a, "Текст дайджеста для A. " + "контекст. " * 12)
        self.assertIsNone(get_cached_compose(key, fp_b))

    def test_disabled(self):
        items = [{"title": "Только одна линия новости"}]
        fp = items_fingerprint(items)
        key = cache_key(user_query="news")
        with patch.dict(os.environ, {"NEWS_DIGEST_CACHE_ENABLED": "false"}, clear=False):
            put_cached_compose(key, fp, "Кэш не должен сохраниться.")
            self.assertIsNone(get_cached_compose(key, fp))


if __name__ == "__main__":
    unittest.main()
