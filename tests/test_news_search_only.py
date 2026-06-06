import asyncio
import os
import re
import unittest
from unittest.mock import AsyncMock, patch

from core.brain_own_turn import (
    news_digest_search_only_enabled,
    news_rss_fallback_enabled,
    pipeline_news_emergency_rss_on_search_fail_enabled,
    pipeline_news_rss_fetch_enabled,
)
from core.telegram_output_guard import (
    collect_news_display_items_from_search,
    is_search_portal_junk,
)


class NewsSearchOnlyTests(unittest.TestCase):
    def test_defaults_search_only_no_rss(self):
        with patch.dict(
            os.environ,
            {
                "NEWS_DIGEST_SEARCH_ONLY": "true",
                "NEWS_RSS_FALLBACK_ENABLED": "true",
                "BRAIN_OWN_TURN_ALLOW_NEWS": "true",
            },
            clear=False,
        ):
            self.assertTrue(news_digest_search_only_enabled())
            self.assertFalse(news_rss_fallback_enabled())
            self.assertFalse(pipeline_news_rss_fetch_enabled("новости"))
            self.assertFalse(pipeline_news_emergency_rss_on_search_fail_enabled())

    def test_collect_from_search_results(self):
        rows = [
            {
                "title": "Беспилотник врезался в жилой дом в Румынии",
                "snippet": "Путин прокомментировал инцидент.",
                "url": "https://example.com/news/drone-romania",
            },
            {
                "title": "ПВО сбила 208 дронов за ночь",
                "snippet": "Минобороны отчиталось о перехватах.",
                "url": "https://news.example.org/pvo",
            },
        ]
        out = collect_news_display_items_from_search(rows)
        self.assertEqual(len(out), 2)
        self.assertTrue(out[0].get("link", "").startswith("http"))

    def test_portal_junk_rejected(self):
        self.assertTrue(
            is_search_portal_junk(
                "РИА Новости - события в Москве, России и мире сегодня: темы дня, фото, видео",
                "Как отдыхаем в июне 2026",
                "https://ria.ru/",
            )
        )
        self.assertTrue(
            is_search_portal_junk("Google Slides: Sign-in", "", "https://docs.google.com/presentation")
        )
        rows = collect_news_display_items_from_search(
            [
                {
                    "title": "РИА Новости - события в Москве, России и мире сегодня",
                    "snippet": "меню SEO",
                    "url": "https://ria.ru/",
                },
                {
                    "title": "В России за ночь сбили 127 беспилотников",
                    "snippet": "Минобороны сообщило о перехватах над несколькими регионами.",
                    "url": "https://example.com/pvo127",
                },
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertIn("беспилот", rows[0]["title"].lower())

    def test_youtube_channel_rejected_from_search_digest(self):
        rows = collect_news_display_items_from_search(
            [
                {
                    "title": "Euronews по-русски - YouTube",
                    "snippet": "Узнавайте о самых важных событиях в Европе",
                    "url": "https://www.youtube.com/@euronewsru",
                },
                {
                    "title": "В России признали нежелательной британскую организацию",
                    "snippet": "Минюст обновил перечень.",
                    "url": "https://ria.ru/20260531/society-123.html",
                },
            ],
            world_feed=True,
            require_article_url=True,
        )
        self.assertEqual(len(rows), 1)
        self.assertIn("ria.ru", rows[0].get("link", ""))

    def test_digest_llm_on_when_search_only(self):
        with patch.dict(
            os.environ,
            {
                "NEWS_DIGEST_SEARCH_ONLY": "true",
                "NEWS_RSS_FALLBACK_ENABLED": "false",
                "NEWS_DIGEST_LLM_SUMMARY": "true",
                "BRAIN_OWN_TURN_ALLOW_NEWS": "false",
            },
            clear=False,
        ):
            from core.news_reply import _news_digest_llm_enabled, news_direct_reply_enabled

            self.assertTrue(_news_digest_llm_enabled())
            self.assertTrue(news_direct_reply_enabled())

    def test_non_news_hosts_rejected(self):
        rows = collect_news_display_items_from_search(
            [
                {
                    "title": "Poki - free online games",
                    "snippet": "Play now on poki.com",
                    "url": "https://poki.com/games",
                },
                {
                    "title": "Путин заявил о скором завершении конфликта",
                    "snippet": "Президент России выступил с заявлением.",
                    "url": "https://ria.ru/20260529/putin-statement.html",
                },
                {
                    "title": "Главная - BBC News Русская служба",
                    "snippet": "BBC News Russian service homepage",
                    "url": "https://www.bbc.com/russian",
                },
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertIn("путин", rows[0]["title"].lower())

    def test_seo_kakie_data_rejected(self):
        from core.telegram_output_guard import _is_seo_kakie_listicle_title

        self.assertTrue(_is_seo_kakie_listicle_title("Какие данные пользователей требуют защиты"))
        self.assertTrue(_is_seo_kakie_listicle_title("Россиянам напомнили, какие данные нельзя передавать"))
        rows = collect_news_display_items_from_search(
            [
                {
                    "title": "Какие данные пользователей цифровых платформ требуют защиты",
                    "snippet": "Эксперт рассказал о цифровизации.",
                    "url": "https://news3.example.com/article/data-protection",
                },
                {
                    "title": "Лукашенко провёл совещание по экономике",
                    "snippet": "Президент обсудил меры поддержки бизнеса.",
                    "url": "https://news.example.com/politics/lukashenko-meeting-2026.html",
                },
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertIn("лукашенко", rows[0]["title"].lower())

    def test_fallback_queries_broad_not_by_profile(self):
        from core.news_reply import news_digest_local_only, news_digest_search_queries

        self.assertFalse(news_digest_local_only("Какие новости"))
        qs = news_digest_search_queries("Какие новости", country="BY", world_feed=False)
        self.assertGreaterEqual(len(qs), 2)
        joined = " ".join(qs).lower()
        self.assertTrue(any(x in joined for x in ("tass", "kommersant", "rbc", "международ")))
        self.assertFalse(all("belta" in q.lower() for q in qs))

    def test_fallback_queries_local_by_explicit(self):
        from core.news_reply import news_digest_local_only, news_digest_search_queries

        self.assertTrue(news_digest_local_only("новости Беларуси сегодня"))
        qs = news_digest_search_queries("новости Беларуси", country="BY", world_feed=False)
        self.assertTrue(
            any(re.search(r"(?i)\bnews\.example\.com\b", q) or "беларус" in q.lower() for q in qs)
        )

    def test_broad_digest_keeps_world_not_only_by(self):
        rows = collect_news_display_items_from_search(
            [
                {
                    "title": "Беларусь ввела новые правила для бизнеса",
                    "snippet": "Правительство утвердило пакет мер.",
                    "url": "https://news.example.com/economics/rules-2026.html",
                },
                {
                    "title": "Путин заявил о переговорах с США",
                    "snippet": "Кремль прокомментировал встречу.",
                    "url": "https://ria.ru/20260530/putin-talks.html",
                },
                {
                    "title": "UN chief warns on climate deadlines",
                    "snippet": "Guterres urged faster action at summit.",
                    "url": "https://news.un.org/story/climate-2026",
                },
            ],
            country="",
            world_feed=False,
        )
        self.assertGreaterEqual(len(rows), 2)
        titles = " ".join(r["title"].lower() for r in rows)
        self.assertIn("путин", titles)

    def test_by_country_filters_sports_junk(self):
        rows = collect_news_display_items_from_search(
            [
                {
                    "title": "PSG XI vs Arsenal FC: Predicted lineup for Champions League final",
                    "snippet": "Team news ahead of the final today.",
                    "url": "https://www.standard.co.uk/sport/football/psg-arsenal",
                },
                {
                    "title": "Лукашенко: Беларусь готовится к возможным вызовам",
                    "snippet": "Президент выступил на форуме в Астане.",
                    "url": "https://www.news.example.com/politics/lukashenko-forum-2026.html",
                },
                {
                    "title": "Belarus bans consumer price rises in bid to tame inflation",
                    "snippet": "President Lukashenko announced measures against inflation.",
                    "url": "https://www.reuters.com/world/europe/belarus-prices-2026/",
                },
            ],
            country="BY",
            world_feed=False,
        )
        self.assertGreaterEqual(len(rows), 1)
        titles = " ".join(r["title"].lower() for r in rows)
        self.assertNotIn("psg", titles)
        self.assertNotIn("arsenal", titles)

    def test_format_search_summary_filters_ddg_junk_for_by(self):
        from core.telegram_output_guard import format_news_from_search

        raw = (
            "Главные новости дня | четверг: Украина, Ливан - UN News; "
            "Какие главные новости ожидаются 30 мая 2026 года? - Vietnam.vn; "
            "Мобильные переводы в Казахстане - Tengrinews; "
            "Лукашенко обсудил налоговую реформу - БелТА"
        )
        out = format_news_from_search(raw, user_query="Какие новости", country="BY")
        low = out.lower()
        self.assertNotIn("vietnam", low)
        self.assertNotIn("tengrinews", low)
        self.assertNotIn("какие главные", low)
        if out:
            self.assertTrue("лукашенко" in low or "бел" in low)

    def test_digest_quality_requires_local_for_by(self):
        from core.news_reply import _digest_quality_sufficient

        junk = [
            {"title": "PSG vs Arsenal final preview", "publisher": "standard.co.uk", "link": "https://x"},
            {"title": "Baseball in 2026", "publisher": "newsweek.com", "link": "https://y"},
            {"title": "World news roundup", "publisher": "bbc.com", "link": "https://z"},
        ]
        good = [
            {
                "title": "Лукашенко о реформе в Беларуси",
                "publisher": "news.example.by",
                "link": "https://news.example.by/politics/x.html",
            },
            {
                "title": "Минск: новый мост открыли",
                "publisher": "news2.example.by",
                "link": "https://news2.example.by/society/bridge-2026.html",
            },
            {
                "title": "Совещание по экономике",
                "publisher": "news3.example.by",
                "link": "https://news3.example.by/society/meeting-2026.html",
            },
        ]
        self.assertFalse(
            _digest_quality_sufficient(junk, country="BY", world_feed=False, min_items=3)
        )
        self.assertTrue(
            _digest_quality_sufficient(good, country="BY", world_feed=False, min_items=3)
        )

    def test_portal_homepages_without_url_rejected(self):
        rows = collect_news_display_items_from_search(
            [
                {
                    "title": "Беларусь | Новости Беларуси | БелТА",
                    "snippet": "Беларусь. Новости об общественно-политической жизни в Республике Беларусь",
                    "url": "",
                },
                {
                    "title": "Новости по теме: Происшествия - СБ. Беларусь сегодня",
                    "snippet": "Происшествия в Беларуси. Криминальная хроника",
                    "url": "https://news3.example.com/",
                },
                {
                    "title": "Новости Беларуси (БелТА) (@beltanews.official) - Instagram",
                    "snippet": "35K followers · 8.5K+ posts",
                    "url": "",
                },
                {
                    "title": "Лукашенко провёл совещание по экономике",
                    "snippet": "Президент обсудил меры поддержки бизнеса.",
                    "url": "https://news.example.com/politics/lukashenko-meeting-2026.html",
                },
            ],
            country="BY",
        )
        self.assertEqual(len(rows), 1)
        self.assertIn("лукашенко", rows[0]["title"].lower())

    def test_portal_digest_reply_detected(self):
        from core.news_reply import _reply_looks_like_portal_digest

        blob = (
            "Главные новости\n\n"
            "1. Беларусь | Новости Беларуси | БелТА\n   · news.example.com\n"
            "2. Новости по теме: Происшествия\n   · news3.example.com\n"
        )
        self.assertTrue(_reply_looks_like_portal_digest(blob))
        self.assertFalse(
            _reply_looks_like_portal_digest("1. Лукашенко о налогах\n   · news.example.com")
        )


class NewsWorldBriefStyleTests(unittest.TestCase):
    def test_world_thematic_search_queries(self):
        from core.news_reply import news_digest_search_queries

        qs = news_digest_search_queries("новости в мире", world_feed=True)
        self.assertGreaterEqual(len(qs), 6)
        blob = " ".join(qs).lower()
        self.assertIn("iran", blob)
        self.assertIn("ukraine", blob)

    def test_gather_digest_sequential_dedupes(self):
        from core.news_reply import _gather_digest_search_rows

        async def run():
            packs = [
                {"ok": True, "results": [{"title": "A", "url": "https://ex.com/a"}]},
                {"ok": True, "results": [{"title": "A", "url": "https://ex.com/a"}, {"title": "B", "url": "https://ex.com/b"}]},
            ]
            with patch(
                "core.news_reply._search_pack",
                AsyncMock(side_effect=packs),
            ):
                return await _gather_digest_search_rows(
                    ["q1", "q2"],
                    country="",
                    user_id="u1",
                    world_feed=True,
                )

        rows = asyncio.run(run())
        self.assertEqual(len(rows), 2)

    def test_gather_early_stop_fewer_searches(self):
        from core.news_reply import _gather_digest_search_rows

        good = [
            {
                "title": "Iran US negotiations Hormuz today",
                "snippet": "Talks continue amid tension.",
                "url": "https://reuters.com/world/iran-deal-2026",
            },
            {
                "title": "Ukraine frontline update drones",
                "snippet": "Military briefing.",
                "url": "https://bbc.com/news/world-europe-ukraine-1",
            },
        ]

        async def run(early: bool):
            calls = 0

            async def _pack(*_a, **_k):
                nonlocal calls
                calls += 1
                return {"ok": True, "results": good}

            env = {"NEWS_DIGEST_GATHER_EARLY_STOP": "true" if early else "false"}
            qs = [f"q{i}" for i in range(6)]
            with patch.dict(os.environ, env, clear=False):
                with patch("core.news_reply._search_pack", AsyncMock(side_effect=_pack)):
                    await _gather_digest_search_rows(
                        qs,
                        country="",
                        user_id="u1",
                        world_feed=True,
                        user_query="какие новости в мире",
                    )
            return calls

        off = asyncio.run(run(False))
        on = asyncio.run(run(True))
        self.assertEqual(off, 6)
        self.assertLess(on, off)
        self.assertGreaterEqual(on, 1)

    def test_narrative_rejects_agent_planning_leak(self):
        from core.news_reply import _narrative_digest_body_usable

        body = (
            "Пользователь просит пример сплошного новостного дайджеста. "
            "План поиска: мировые новости, конфликты, экономика."
            + "x" * 200
        )
        self.assertFalse(
            _narrative_digest_body_usable(body, narrative_style="world_brief")
        )

    def test_resolve_world_brief_for_world_query(self):
        from core.news_reply import _resolve_narrative_style

        self.assertEqual(
            _resolve_narrative_style(user_query="новости в мире", world_feed=True),
            "world_brief",
        )
        self.assertEqual(
            _resolve_narrative_style(user_query="какие новости", world_feed=False),
            "world_brief",
        )
        self.assertEqual(
            _resolve_narrative_style(user_query="новости беларуси", world_feed=False),
            "per_item",
        )

    def test_world_dated_header(self):
        from core.telegram_output_guard import _news_digest_header

        head = _news_digest_header("новости в мире")
        self.assertRegex(head, r"^Главные мировые новости на \d{1,2} ")
        self.assertIn("года", head)

    def test_world_narrative_footer(self):
        from core.telegram_output_guard import _news_narrative_footer

        foot = _news_narrative_footer(world_feed=True, user_query="новости в мире")
        self.assertIn("открытых источников", foot.lower())

    def test_finish_narrative_digest_wraps_header_footer(self):
        from core.news_reply import _finish_narrative_digest

        body = (
            "Сегодня в центре мировых событий — переговоры и эскалация на фронте.\n\n"
            "Второй абзац с контекстом по первой теме из заголовков ленты.\n\n"
            "Третий абзац про экономику и энергетику по данным открытых источников."
            + "x" * 120
        )
        out = _finish_narrative_digest(
            body,
            user_query="новости в мире",
            world_feed=True,
            narrative_style="world_brief",
        )
        self.assertRegex(out, r"^Главные мировые новости на \d")
        self.assertIn("Сегодня в центре мировых событий", out)
        self.assertIn("открытых источников", out.lower())

    def test_world_brief_body_usable(self):
        from core.news_reply import _narrative_digest_body_usable

        body = (
            "Сегодня в центре мировых событий — сделка по Ирану и удары на Украине.\n\n"
            "Трамп говорит о финальной стадии соглашения с Ираном и проливе Ормуз.\n\n"
            "На востоке Украины сообщают о массированных ударах и дронах.\n\n"
            "МВФ предупреждает о нагрузке на мировые энергопоставки."
            + "z" * 100
        )
        shown = [
            {"title": "Trump says Iran deal near final stage", "snippet": "Hormuz blockade discussed"},
            {"title": "Heavy strikes reported in eastern Ukraine", "snippet": "drones and missiles"},
            {"title": "IMF warns on energy supply strain", "snippet": "Middle East war impact"},
        ]
        self.assertTrue(
            _narrative_digest_body_usable(body, displayed=shown, narrative_style="world_brief")
        )


class NewsNarrativeCrossLangTests(unittest.TestCase):
    def test_russian_narrative_usable_with_english_headlines(self):
        from core.news_reply import _narrative_digest_body_usable

        shown = [
            {
                "title": "United States announces blockade on the Strait of Hormuz",
                "snippet": "oil prices briefly rose",
            },
            {
                "title": "Morning Digest: U.S.-Iran agree to 60-day ceasefire extension",
                "snippet": "Trump sign-off awaited",
            },
            {
                "title": "UN chief warns on climate deadlines",
                "snippet": "Guterres urged faster action",
            },
        ]
        body = (
            "Соединённые Штаты объявили о блокаде Ормузского пролива, что подняло цены на нефть.\n\n"
            "Стороны обсуждают 60-дневное продление перемирия между США и Ираном.\n\n"
            "Генсек ООН предупредил о сжатых сроках по климатической повестке на саммите."
            + "x" * 80
        )
        self.assertTrue(_narrative_digest_body_usable(body, displayed=shown))

    def test_same_language_overlap_still_required(self):
        from core.news_reply import _narrative_digest_body_usable

        shown = [
            {"title": "Quantum computing breakthrough announced", "snippet": "lab results"},
            {"title": "Stock markets rally on tech earnings", "snippet": "NASDAQ up"},
            {"title": "Climate summit opens in Geneva", "snippet": "delegates arrive"},
        ]
        body = (
            "This paragraph discusses unrelated cooking recipes and travel tips only. "
            "Nothing about markets, quantum, or climate appears here at all."
            + "y" * 80
        )
        self.assertFalse(_narrative_digest_body_usable(body, displayed=shown))

    def test_search_only_narrative_fail_falls_back_to_list(self):
        from core.news_reply import _compose_digest_reply

        displayed = [
            {
                "index": 1,
                "title": "UN chief warns on climate deadlines",
                "publisher": "news.un.org",
                "snippet": "Guterres urged faster action at summit.",
                "link": "https://news.un.org/story/climate-2026",
            },
            {
                "index": 2,
                "title": "Oil prices ease after Hormuz tensions",
                "publisher": "reuters.com",
                "snippet": "Markets reacted to diplomatic signals.",
                "link": "https://www.reuters.com/world/oil-2026",
            },
        ]

        async def run():
            with patch.dict(
                os.environ,
                {
                    "NEWS_DIGEST_FORMAT": "narrative",
                    "NEWS_DIGEST_SEARCH_ONLY": "true",
                    "NEWS_DIGEST_LLM_SUMMARY": "true",
                    "NEWS_RSS_FALLBACK_ENABLED": "false",
                },
                clear=False,
            ):
                with patch("core.news_reply._news_digest_llm_enabled", return_value=True):
                    with patch(
                        "core.news_reply._llm_digest_narrative_brief",
                        AsyncMock(return_value=""),
                    ):
                        return await _compose_digest_reply(
                            displayed, user_query="новости в мире", user_id="u1"
                        )

        out = asyncio.run(run())
        self.assertIn("UN chief", out)
        self.assertRegex(out, r"(?m)^\s*1\.\s+")
        self.assertNotIn("не удалось оформить", out.lower())


class NewsSearchUltimateFixTests(unittest.TestCase):
    def test_require_article_url_filters_portal_homepage(self):
        rows = collect_news_display_items_from_search(
            [
                {
                    "title": "Interfax.ru - Интерфакс: новости",
                    "snippet": "меню SEO",
                    "url": "https://www.interfax.ru/",
                },
                {
                    "title": "Путин заявил о переговорах с США",
                    "snippet": "Кремль прокомментировал встречу.",
                    "url": "https://ria.ru/20260530/putin-talks.html",
                },
            ],
            require_article_url=True,
        )
        self.assertEqual(len(rows), 1)
        self.assertIn("путин", rows[0]["title"].lower())

    def test_relaxed_quality_one_article_two_items(self):
        from core.news_reply import _digest_quality_sufficient

        items = [
            {
                "title": "UN chief warns on climate",
                "link": "https://news.un.org/story/climate-2026",
                "snippet": "Guterres urged action.",
            },
            {
                "title": "Oil prices ease after tensions",
                "link": "https://www.reuters.com/world/oil-2026",
                "snippet": "Markets reacted.",
            },
        ]
        self.assertFalse(
            _digest_quality_sufficient(items, country="", world_feed=True, min_items=3)
        )
        self.assertTrue(
            _digest_quality_sufficient(
                items, country="", world_feed=True, min_items=2, relaxed=True
            )
        )

    def test_seed_prefetch_dedupes(self):
        from core.news_reply import _seed_search_raw_from_prefetch

        rows = _seed_search_raw_from_prefetch(
            [
                {"title": "A", "url": "https://example.com/a"},
                {"title": "A", "url": "https://example.com/a"},
            ],
            "",
        )
        self.assertEqual(len(rows), 1)

    def test_validate_news_world_narrative_ok(self):
        from core.reform_probe_support import validate_news_world_reply

        narr = (
            "Соединённые Штаты объявили о блокаде пролива.\n\n"
            "Стороны обсуждают продление перемирия между США и Ираном.\n\n"
            "Генсек ООН предупредил о климатической повестке на саммите."
            + "x" * 80
        )
        errs = validate_news_world_reply(narr)
        self.assertNotIn("news_no_numbered_digest", errs)


if __name__ == "__main__":
    unittest.main()
