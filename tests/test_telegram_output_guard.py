"""Регрессия: дедуп исходящих сообщений и формат новостей."""

import unittest

from core.models import Output
from core.telegram_output_guard import (
    _news_digest_header,
    build_news_llm_source_block,
    dedupe_identical_text_outputs,
    dedupe_telegram_outputs,
    enrich_news_items_with_snippets,
    format_news_from_items,
    format_news_from_search,
    keep_single_best_text_output,
    should_skip_duplicate_photo_turn,
    trim_hallucinated_news_bullets,
)


class TelegramOutputGuardTests(unittest.TestCase):
    def test_keep_single_best_drops_stale_topic(self):
        user = "почему свет такой быстрый"
        stale = Output(
            type="text",
            payload=(
                "Анальный сфинктер — это кольцо мышц, которое удерживает кал в прямой "
                "кишке и позволяет контролировать процесс дефекации."
            ),
            meta={},
        )
        good = Output(
            type="text",
            payload=(
                "Свет в вакууме распространяется со скоростью около 300 тысяч километров "
                "в секунду — это предел скорости для любых сигналов и частиц с массой."
            ),
            meta={},
        )
        kept = keep_single_best_text_output([stale, good], user)
        substantive = [o for o in kept if len(str(o.payload or "")) >= 80]
        self.assertEqual(len(substantive), 1)
        self.assertIn("скорост", str(substantive[0].payload).lower())

    def test_dedupe_drops_low_relevance_second_answer(self):
        user = "какой цвет стула в комнате"
        main = Output(
            type="text",
            payload=(
                "Алексей, для стула в гостиной часто выбирают нейтральные оттенки: "
                "бежевый, серый или тёплый дуб — они не спорят с интерьером."
            ),
            meta={},
        )
        off_topic = Output(
            type="text",
            payload=(
                "Алексей, в ванной комнате важно продумать сантехнику и вентиляцию. "
                "Унитаз лучше ставить с учётом размера помещения."
            ),
            meta={},
        )
        kept = dedupe_telegram_outputs([main, off_topic], user)
        self.assertEqual(len(kept), 1)
        self.assertIn("стул", str(kept[0].payload).lower())

    def test_dedupe_identical_text(self):
        a = Output(type="text", payload="Текст на изображении не найден.", meta={})
        b = Output(type="text", payload="Текст на изображении не найден.", meta={})
        kept = dedupe_identical_text_outputs([a, b])
        self.assertEqual(len(kept), 1)

    def test_news_format_caps_items(self):
        parts = [
            f"Путин заявил о переговорах по Ukraine track {i} - Example News"
            for i in range(1, 15)
        ]
        raw = "; ".join(parts)
        out = format_news_from_search(raw, user_query="новости")
        self.assertIn("1.", out)
        self.assertIn("Путин заявил", out)
        self.assertNotIn("track 14", out)

    def test_news_rss_items_clean_format(self):
        items = [
            {
                "title": "NATO chief warns on air defense - Reuters",
                "link": "https://reuters.com/a",
                "source": "https://reuters.com",
                "source_name": "Reuters",
            },
            {
                "title": "Euroclear ruling in Moscow - Iz.ru",
                "link": "https://iz.ru/b",
                "source": "https://iz.ru",
                "source_name": "Iz.ru",
            },
        ]
        out = format_news_from_items(items, user_query="новости в мире")
        self.assertRegex(out, r"^Главные мировые новости на \d{1,2} ")
        self.assertIn("1. NATO chief warns on air defense", out)
        self.assertIn("· Reuters", out)
        self.assertNotIn("reuters.com", out)
        self.assertNotIn("Кратко:", out)
        self.assertNotIn("(новости в мире)", out)

    def test_news_header_generic_query(self):
        items = [
            {
                "title": "Headline A with enough length for filter",
                "link": "https://bbc.com/a",
                "source": "https://bbc.com",
                "source_name": "BBC",
            }
        ]
        out = format_news_from_items(items, user_query="какие новости")
        self.assertRegex(out, r"^Главные мировые новости на \d{1,2} ")
        self.assertNotIn("Кратко:", out)
        self.assertNotIn("(какие новости)", out)

    def test_news_header_topic_query(self):
        items = [
            {
                "title": "Headline B with enough length for filter",
                "link": "https://bbc.com/b",
                "source": "https://bbc.com",
                "source_name": "BBC",
            }
        ]
        out = format_news_from_items(items, user_query="новости в мире")
        self.assertRegex(out, r"^Главные мировые новости на \d{1,2} ")

    def test_news_search_header(self):
        raw = (
            "Путин заявил о готовности к переговорам: "
            "пресс-секретарь прокомментировал сроки - Reuters"
        )
        out = format_news_from_search(raw, user_query="какие новости")
        self.assertRegex(out, r"^Главные мировые новости на \d{1,2} ")
        self.assertNotIn("веб-поиск", out.lower())
        self.assertNotIn("(показаны первые", out.lower())

    def test_news_skips_duplicate_publisher(self):
        items = [
            {
                "title": "Netanyahu warning on Iran war tensions - NDTV",
                "link": "https://www.ndtv.com/a",
                "source": "https://www.ndtv.com",
                "source_name": "NDTV",
            },
            {
                "title": "Second headline from same outlet blocked - NDTV",
                "link": "https://www.ndtv.com/b",
                "source": "https://www.ndtv.com",
                "source_name": "NDTV",
            },
            {
                "title": "World Cup squad dilemmas facing coach - BBC",
                "link": "https://www.bbc.com/c",
                "source": "https://www.bbc.com",
                "source_name": "BBC",
            },
        ]
        out = format_news_from_items(items, user_query="новости")
        self.assertEqual(out.count("NDTV"), 1)
        self.assertIn("BBC", out)

    def test_build_news_llm_source_block_includes_snippet(self):
        items = [
            {
                "title": "NATO chief warns on air defense - Reuters",
                "source_name": "Reuters",
                "snippet": "Alliance discusses eastern flank missile systems after recent drills.",
            }
        ]
        block = build_news_llm_source_block(items)
        self.assertIn("1. NATO chief warns", block)
        self.assertIn("Выдержка:", block)
        self.assertIn("eastern flank", block)
        self.assertIn("Источник: Reuters", block)

    def test_enrich_news_items_with_snippets(self):
        items = [{"title": "NATO chief warns on air defense - Reuters", "source_name": "Reuters"}]
        search = [
            {
                "title": "NATO chief warns on air defense",
                "snippet": "Alliance discusses eastern flank missile systems after recent drills.",
            }
        ]
        enriched = enrich_news_items_with_snippets(items, search)
        self.assertIn("snippet", enriched[0])
        out = format_news_from_items(enriched, user_query="новости")
        self.assertIn("eastern flank", out)

    def test_trim_hallucinated_bullets(self):
        body = "\n".join(f"{i}. Пункт {i}" for i in range(1, 12))
        trimmed = trim_hallucinated_news_bullets(body, max_items=7)
        self.assertIn("7. Пункт", trimmed)
        self.assertNotIn("8. Пункт", trimmed)
        self.assertNotIn("показаны первые", trimmed.lower())

    def test_photo_dedup_window(self):
        uid, cid, fid = "1", "2", "abc"
        self.assertFalse(should_skip_duplicate_photo_turn(uid, cid, fid))
        self.assertTrue(should_skip_duplicate_photo_turn(uid, cid, fid))


if __name__ == "__main__":
    unittest.main()
