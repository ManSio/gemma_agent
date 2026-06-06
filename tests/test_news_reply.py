import os
import unittest
from unittest.mock import AsyncMock, patch

from core.brain.text_helpers import (
    _body_looks_like_news_digest,
    looks_like_news_story_deep_followup,
    parse_news_item_pick_index,
    resolve_affirmative_search_query,
    resolve_news_item_pick_index,
    wttr_in_j1_url,
)
from core.news_reply import (
    _extract_story_search_query,
    _match_digest_item_by_user_query,
    _news_digest_narrative_style,
    _rss_items_are_google_meta_only,
    persist_news_digest_from_assistant_reply,
    stash_news_digest_context,
    try_news_item_reply,
    try_news_item_reply_sync,
    try_news_reply_sync,
)
from core.telegram_output_guard import collect_news_display_items, parse_numbered_news_digest_items


DIGEST = """Главные новости

1. Число погибших при теракте
   · Ведомости
2. МИД Албании вызвал посла России
   · rtvi.com

Напишите номер пункта или «развёрнуто» — расскажу подробнее."""

RECENT = [
    {"role": "user", "text": "главные новости"},
    {"role": "assistant", "text": DIGEST},
]

_LEGACY_NEWS_ITEM_ENV = {
    "BRAIN_OWN_TURN_ENABLED": "true",
    "BRAIN_OWN_TURN_ALLOW_NEWS_ITEM": "true",
    "BRAIN_OWN_TURN_ALLOW_NEWS": "true",
    "NEWS_RSS_FALLBACK_ENABLED": "true",
}


class NewsReplyTests(unittest.TestCase):
    def test_headlines_request_returns_digest(self):
        fake_rss = {
            "configured": True,
            "items": [
                {
                    "title": "Заголовок — Example.com",
                    "link": "https://example.com/a",
                    "source_name": "Example",
                }
            ],
        }

        async def _headlines(topic: str = "", country: str = ""):
            return fake_rss

        legacy_news_env = {
            "BRAIN_OWN_TURN_ENABLED": "true",
            "BRAIN_OWN_TURN_ALLOW_NEWS": "true",
            "NEWS_RSS_FALLBACK_ENABLED": "true",
            "NEWS_DIGEST_SEARCH_ONLY": "false",
            "NEWS_DIGEST_FORMAT": "list",
        }
        with patch.dict(os.environ, legacy_news_env, clear=False):
            with patch("modules.external_apis.clients.NewsAPIClient") as cls:
                inst = cls.return_value
                inst.wants_world_news = lambda _q: False
                inst.headlines = AsyncMock(side_effect=_headlines)
                out = try_news_reply_sync("что нового в новостях", persisted={"user_facts": {}})
        self.assertIsNotNone(out)
        self.assertIn("Заголовок", out or "")

    def test_rss_google_meta_only_detected(self):
        items = [
            {"title": "Google Новости - В мире", "link": "https://news.google.com/"},
            {"title": "Приложение Google Новости", "link": "https://news.google.com/"},
        ]
        self.assertTrue(_rss_items_are_google_meta_only(items))
        self.assertFalse(
            _rss_items_are_google_meta_only(
                [{"title": "Заголовок — Example.com", "link": "https://example.com/a"}]
            )
        )

    def test_world_news_prefers_search_over_google_meta_rss(self):
        """Prod search-only: article URLs из поиска, не Google-meta RSS."""
        from core.heuristic_context_gate import GateResult

        fake_search = {
            "ok": True,
            "summary": "",
            "results": [
                {
                    "title": "Событие в Европе: переговоры продолжаются",
                    "snippet": "Reuters о встрече министров.",
                    "url": "https://www.reuters.com/world/europe/example-2026",
                },
                {
                    "title": "Министры ЕС встретились в Брюсселе",
                    "snippet": "Обсуждение санкций.",
                    "url": "https://www.bbc.com/news/world-europe-example",
                },
            ],
        }
        env = {
            "BRAIN_OWN_TURN_ENABLED": "true",
            "BRAIN_OWN_TURN_ALLOW_NEWS": "true",
            "NEWS_DIGEST_SEARCH_ONLY": "true",
            "NEWS_RSS_FALLBACK_ENABLED": "true",
            "NEWS_DIGEST_FORMAT": "list",
            "NEWS_DIGEST_LLM_MODEL": "",
        }
        gate_ok = GateResult(verdict="allowed", rule_id="news_direct", reason="test")
        digest = "Главные новости\n\n1. Событие в Европе\n   · Reuters"
        with patch.dict(os.environ, env, clear=False):
            with patch(
                "core.heuristic_context_gate.should_run_shortcut_async",
                new_callable=AsyncMock,
                return_value=gate_ok,
            ):
                with patch("core.universal_search_module.UniversalSearchModule") as usm:
                    usm.return_value.search = AsyncMock(return_value=fake_search)
                    with patch(
                        "core.news_reply.compose_news_digest_from_search",
                        new_callable=AsyncMock,
                        return_value=digest,
                    ):
                        with patch(
                            "modules.external_apis.clients.NewsAPIClient"
                        ) as cls:
                            inst = cls.return_value
                            inst.wants_world_news = lambda _q: True
                            inst.headlines = AsyncMock()
                            out = try_news_reply_sync(
                                "Какие новости в мире",
                                persisted={"user_facts": {}},
                            )
        self.assertEqual(out, digest)
        inst.headlines.assert_not_called()
        self.assertNotIn("Google", out or "")

    def test_non_news_returns_none(self):
        out = try_news_reply_sync("привет", persisted={"user_facts": {}})
        self.assertIsNone(out)

    def test_news_direct_skipped_when_user_rejects_rss(self):
        """G1: явный отказ от RSS — не news_direct, ход уходит в brain + UniversalSearch."""
        legacy_news_env = {
            "BRAIN_OWN_TURN_ENABLED": "true",
            "BRAIN_OWN_TURN_ALLOW_NEWS": "true",
            "NEWS_RSS_FALLBACK_ENABLED": "true",
            "NEWS_DIGEST_FORMAT": "list",
            "NEWS_RESPECT_USER_SEARCH_OVER_RSS": "true",
        }
        with patch.dict(os.environ, legacy_news_env, clear=False):
            out = try_news_reply_sync(
                "последние новости Беларуси не через rss",
                persisted={"user_facts": {"country": "BY"}},
            )
        self.assertIsNone(out)

    def test_g2_news_brief_profile_includes_universal_search(self):
        """Q2 G2: профиль news_brief — путь UniversalSearch + UrlFetch (не RSS в plan)."""
        from core.brain.profile_registry import get_profile

        cfg = get_profile("news_brief")
        fam = {str(x) for x in (cfg.tool_families or ())}
        self.assertTrue(any(x.startswith("UniversalSearch") for x in fam))
        self.assertTrue(any(x.startswith("UrlFetch") for x in fam))

    def test_parse_news_item_pick_bare_digit(self):
        self.assertEqual(parse_news_item_pick_index("2", RECENT), 2)
        self.assertIsNone(parse_news_item_pick_index("2", []))

    def test_bare_digit_not_news_after_penteract_answer(self):
        recent = [
            {
                "role": "assistant",
                "text": "1. У пентеракта 10 четырёхмерных ячеек.\n2. Трёхмерных граней — 5.",
            },
        ]
        self.assertIsNone(parse_news_item_pick_index("4", recent))

    def test_persist_news_digest_from_assistant_reply(self):
        persisted: dict = {"dialogue_state": {}}
        digest = "1. Первая новость\n   · Источник\n2. Вторая новость\n   · Другой"
        persist_news_digest_from_assistant_reply(digest, persisted=persisted, context={})
        items = persisted.get("dialogue_state", {}).get("last_news_digest_items")
        self.assertIsInstance(items, list)
        self.assertGreaterEqual(len(items), 2)

    def test_affirmative_search_only_after_explicit_offer(self):
        recent = [
            {"role": "user", "text": "Кратко: что известно про певца Иванова в новостях?"},
            {"role": "assistant", "text": "Кратко: задержан 21 мая. Могу перепроверить поиском в интернете."},
        ]
        q = resolve_affirmative_search_query("да", recent, None)
        self.assertIsNotNone(q)
        self.assertIn("Иванов", q or "")

    def test_affirmative_not_after_country_confirm(self):
        from core.brain.text_helpers import (
            affirmative_overrides_fact_confirmation,
            resolve_affirmative_search_query,
        )

        recent = [
            {"role": "user", "text": "моя страна Беларусь"},
            {"role": "assistant", "text": "Запомнить страну? Ответь «да» или «нет»."},
        ]
        persisted = {
            "pending_facts_confirmation": {
                "country": {"value": "Беларусь", "field": "country"},
            },
            "facts_flow": {
                "confirmation_prompt": "Запомнить страну?",
            },
        }
        self.assertIsNone(resolve_affirmative_search_query("да", recent, persisted))
        self.assertFalse(
            affirmative_overrides_fact_confirmation("да", recent_dialogue=recent, persisted=persisted)
        )

    def test_affirmative_not_from_stale_news_in_history(self):
        recent = [
            {"role": "user", "text": "какие новости"},
            {"role": "assistant", "text": "Главные мировые новости на сегодня…"},
            {"role": "user", "text": "моя страна Беларусь"},
            {"role": "assistant", "text": "Запомнить страну? Ответь «да» или «нет»."},
        ]
        self.assertIsNone(resolve_affirmative_search_query("да", recent, None))

    def test_wttr_minsk_uses_latin_slug(self):
        url = wttr_in_j1_url("Минск", "BY")
        self.assertIn("Minsk", url)
        self.assertIn("lang=ru", url)

    def test_resolve_pick_on_podrobnee_after_item(self):
        recent = RECENT + [
            {
                "role": "assistant",
                "text": "2. МИД Албании вызвал посла России\n· rtvi.com\n\nТекст новости.",
            },
        ]
        self.assertEqual(resolve_news_item_pick_index("подробнее", recent), 2)
        self.assertEqual(resolve_news_item_pick_index("подробние", recent), 2)
        self.assertIsNone(resolve_news_item_pick_index("развёрнуто", RECENT))

    def test_build_news_search_query_quotes_kak_headline(self):
        from core.news_reply import _build_news_search_query

        q = _build_news_search_query(
            "Как новости влияют на российский фондовый рынок",
            "БКС Экспресс",
        )
        self.assertIn("БКС", q)
        self.assertTrue(q.startswith('"') or "БКС" in q)

    def test_digest_items_prefer_assistant_text_over_stale_cache(self):
        from core.news_reply import _digest_items_from_dialogue

        llm_digest = """1. Пункт один про свет
2. Второй пункт
3. Третий
4. Арест певца Петра Иванова и магнитные бури"""
        recent = [
            {"role": "user", "text": "новости"},
            {"role": "assistant", "text": llm_digest},
        ]
        persisted = {
            "dialogue_state": {
                "last_news_digest_items": [
                    {"index": 4, "title": "Старая лента ООН Эбола", "link": "http://x"},
                ]
            }
        }
        items = _digest_items_from_dialogue(persisted, recent)
        self.assertGreaterEqual(len(items), 4)
        self.assertIn("Иванова", items[3]["title"])

    def test_merge_parsed_digest_keeps_rss_links_by_index(self):
        from core.news_reply import _merge_parsed_digest_with_stash

        parsed = [
            {
                "index": 1,
                "title": "**Ракетный удар по зданию Центробанка в Севастополе**",
                "publisher": "Новости Mail",
            }
        ]
        cached = [
            {
                "index": 1,
                "title": "ЦБ в Севастополе: ракетный удар",
                "publisher": "Mail.ru",
                "link": "https://news.google.com/rss/articles/sevastopol-cb",
                "source_url": "https://news.mail.ru/sevastopol-cb-hit/",
            }
        ]
        merged = _merge_parsed_digest_with_stash(parsed, cached)
        self.assertEqual(1, merged[0]["index"])
        self.assertIn("mail.ru", str(merged[0].get("source_url") or "").lower())
        self.assertNotIn("**", merged[0]["title"])

    def test_anchors_reject_odessa_article_for_sevastopol_title(self):
        from core.news_reply import _anchors_satisfied, _text_relevant_to_title

        title = "Ракетный удар по зданию Центробанка в Севастополе"
        odessa = (
            "Навел двойной ракетный удар по Одессе: задержан священник. "
            "По версии следствия, он скорректировал удар баллистическими ракетами."
        )
        self.assertFalse(_anchors_satisfied(title, odessa, url="https://dumskaya.net/news/odesa/"))
        self.assertFalse(_text_relevant_to_title(title, odessa))

    def test_focused_query_from_named_entity_line(self):
        from core.news_reply import _build_news_search_query

        q = _build_news_search_query(
            "Также среди заметных событий — арест певца Петра Иванова",
            "",
        )
        self.assertIn("Иванов", q)
        self.assertIn("арест", q.lower())

    def test_disambiguation_snippet_hidden_in_digest(self):
        from core.telegram_output_guard import (
            _format_news_item_block,
            _looks_like_disambiguation_snippet,
        )

        bad = "Как: Как в русском языке наречие, см. Викисловарь"
        self.assertTrue(_looks_like_disambiguation_snippet(bad, "Как новости влияют"))
        block = _format_news_item_block(
            2,
            title="Как новости влияют на российский фондовый рынок",
            snippet=bad,
            publisher="БКС Экспресс",
        )
        self.assertNotIn("Викисловарь", block)

    def test_parse_numbered_digest_items(self):
        items = parse_numbered_news_digest_items(DIGEST)
        self.assertEqual(len(items), 2)
        self.assertIn("Албании", items[1]["title"])

    def test_stash_keeps_display_order_and_links(self):
        rss = [
            {
                "title": "Первая — SiteA",
                "link": "https://news.google.com/a",
                "source": "https://sitea.example/news/1",
                "source_name": "SiteA",
            },
            {
                "title": "Вторая — SiteB",
                "link": "https://news.google.com/b",
                "source": "https://siteb.example/article",
                "source_name": "SiteB",
            },
        ]
        persisted: dict = {}
        shown = stash_news_digest_context(persisted, rss, query="мировые новости", world_feed=True)
        self.assertEqual(len(shown), 2)
        from core.site_recipe_engine import host_matches

        self.assertTrue(host_matches(str(shown[0].get("link") or ""), "news.google.com"))
        self.assertTrue(host_matches(str(shown[1].get("link") or ""), "news.google.com"))
        ds = persisted.get("dialogue_state") or {}
        self.assertEqual(len(ds.get("last_news_digest_items") or []), 2)
        displayed = collect_news_display_items(rss, user_query="мировые новости")
        self.assertEqual(displayed[0]["title"], shown[0]["title"])

    def test_item_pick_returns_detail_via_search(self):
        import asyncio

        title2 = "\u041c\u0418\u0414 \u0410\u043b\u0431\u0430\u043d\u0438\u0438 \u0432\u044b\u0437\u0432\u0430\u043b \u043f\u043e\u0441\u043b\u0430 \u0420\u043e\u0441\u0441\u0438\u0438"
        detail = (
            title2
            + " \u0434\u043b\u044f \u043a\u043e\u043d\u0441\u0443\u043b\u044c\u0442\u0430\u0446\u0438\u0439 \u043f\u043e\u0441\u043b\u0435 \u0443\u0434\u0430\u0440\u0430."
        )
        persisted = {
            "user_facts": {},
            "dialogue_state": {
                "last_news_digest_items": [
                    {"index": 1, "title": "a", "publisher": "b", "snippet": ""},
                    {"index": 2, "title": title2, "publisher": "rtvi.com", "snippet": ""},
                ]
            },
        }
        recent = [
            {"role": "user", "text": "news"},
            {"role": "assistant", "text": "x\n\n1. a\n2. " + title2},
        ]

        async def run():
            return await try_news_item_reply(
                "2", persisted=persisted, recent_dialogue=recent
            )

        with patch.dict(os.environ, _LEGACY_NEWS_ITEM_ENV, clear=False):
            with patch(
                "core.news_reply._fetch_news_item_article",
                new=AsyncMock(return_value={"text": detail, "images": [], "url": ""}),
            ):
                out = asyncio.run(run())
        self.assertIsNotNone(out)
        self.assertIn("\u0410\u043b\u0431\u0430\u043d\u0438\u0438", out or "")
        self.assertIn("\u043a\u043e\u043d\u0441\u0443\u043b\u044c\u0442\u0430\u0446\u0438\u0439", out or "")

    def test_fetch_detail_via_search_uses_pack(self):
        import asyncio
        from core.news_reply import _fetch_detail_via_search

        title = "\u041c\u0418\u0414 \u0410\u043b\u0431\u0430\u043d\u0438\u0438 \u0432\u044b\u0437\u0432\u0430\u043b \u043f\u043e\u0441\u043b\u0430 \u0420\u043e\u0441\u0441\u0438\u0438"
        summary = (
            title
            + " \u0434\u043b\u044f \u043a\u043e\u043d\u0441\u0443\u043b\u044c\u0442\u0430\u0446\u0438\u0439 \u043f\u043e\u0441\u043b\u0435 \u0443\u0434\u0430\u0440\u0430."
        )

        async def _pack(q, *, country="", user_id="", timeout=0.0, tag=""):
            return {"ok": True, "summary": summary}

        async def _run():
            with patch("core.news_reply._search_pack", AsyncMock(side_effect=_pack)):
                return await _fetch_detail_via_search(
                    title,
                    "rtvi",
                    user_id="u",
                    country="BY",
                    timeout=10.0,
                )

        got = asyncio.run(_run())
        body = got.get("text") if isinstance(got, dict) else got
        self.assertIn("\u043a\u043e\u043d\u0441\u0443\u043b\u044c\u0442\u0430\u0446\u0438\u0439", body or "")

    def test_item_pick_resolves_rss_and_fetches_page(self):
        unesco_digest = [
            {"role": "user", "text": "мировые новости"},
            {
                "role": "assistant",
                "text": (
                    "Мировые новости\n\n"
                    "1. Первая\n   · A\n"
                    "2. Международный симпозиум Золотая Орда Астана UNESCO\n   · UNESCO\n"
                ),
            },
        ]

        async def _headlines(topic: str = "", country: str = ""):
            return {
                "configured": True,
                "items": [
                    {
                        "title": "Международный симпозиум Золотая Орда — UNESCO",
                        "link": "https://news.google.com/rss/articles/abc",
                        "source": "https://www.unesco.org/symposium",
                        "source_name": "UNESCO",
                    }
                ],
            }

        async def _fetch_page(url: str, user_id: str = "", **kwargs):
            return {
                "ok": True,
                "text": (
                    "В Астане под эгидой ЮНЕСКО прошёл международный симпозиум, "
                    "посвящённый истории Золотой Орды. " * 4
                ),
            }

        async def _search_pack(q, *, country="", user_id="", timeout=0.0, tag=""):
            return {
                "ok": True,
                "results": [
                    {
                        "url": "https://www.unesco.org/symposium/golden-horde-2026",
                        "snippet": (
                            "В Астане под эгидой ЮНЕСКО прошёл международный симпозиум, "
                            "посвящённый истории Золотой Орды."
                        ),
                        "title": "Симпозиум UNESCO",
                    }
                ],
            }

        with patch.dict(os.environ, _LEGACY_NEWS_ITEM_ENV, clear=False):
            with patch("modules.external_apis.clients.NewsAPIClient") as news_cls:
                news_cls.return_value.headlines = AsyncMock(side_effect=_headlines)
                with patch("core.news_reply._search_pack", AsyncMock(side_effect=_search_pack)):
                    with patch("core.url_fetch.UrlFetchModule") as fetch_cls:
                        fetch_cls.return_value.fetch_page = AsyncMock(side_effect=_fetch_page)
                        out = try_news_item_reply_sync(
                            "2",
                            persisted={"user_facts": {}},
                            recent_dialogue=unesco_digest,
                        )
        self.assertIsNotNone(out)
        self.assertIn("Астане", out or "")
        self.assertNotIn("Краткого текста", out or "")


    def test_google_news_url_skipped_for_direct_fetch(self):
        from core.news_reply import _is_google_news_url, _looks_like_consent_wall, _url_looks_like_article

        g = "https://news.google.com/rss/articles/CBMiTEST"
        self.assertTrue(_is_google_news_url(g))
        self.assertFalse(_url_looks_like_article("https://amur.life/"))
        self.assertTrue(_looks_like_consent_wall("Важная информация\nRU\nВсе языки\nEnglish"))

    def test_news_direct_gated_by_brain_own_turn(self):
        from core.news_reply import news_direct_reply_enabled, news_item_pick_enabled

        with patch.dict(
            os.environ,
            {
                "BRAIN_OWN_TURN_ENABLED": "true",
                "BRAIN_OWN_TURN_ALLOW_NEWS": "false",
                "BRAIN_OWN_TURN_ALLOW_NEWS_ITEM": "false",
                "BRAIN_NEWS_ITEM_REPLY_ENABLED": "true",
                "NEWS_DIGEST_SEARCH_ONLY": "false",
            },
            clear=False,
        ):
            self.assertFalse(news_direct_reply_enabled())
            self.assertTrue(news_item_pick_enabled())
        with patch.dict(
            os.environ,
            {"BRAIN_OWN_TURN_ALLOW_NEWS": "true", "BRAIN_OWN_TURN_ALLOW_NEWS_ITEM": "true"},
            clear=False,
        ):
            self.assertTrue(news_direct_reply_enabled())
            self.assertTrue(news_item_pick_enabled())

    def test_search_pack_searx_first_for_news_tag(self):
        import asyncio
        from core.news_reply import _search_pack

        calls: list[str] = []

        async def _ddg(q):
            calls.append("ddg")
            return {"configured": True, "summary": "ok", "results": []}

        async def _univ(q, country="", user_id="", searx_categories=""):
            calls.append("univ")
            return {
                "ok": True,
                "results": [
                    {
                        "title": "Лукашенко обсудил налоговую реформу на совещании",
                        "url": "https://news.example.com/politics/reform.html",
                        "snippet": "Президент перечислил ключевые меры.",
                    }
                ],
            }

        async def run():
            with patch("core.news_reply.GenericSearchClient", create=True) as cls:
                cls.return_value.search = AsyncMock(side_effect=_ddg)
                with patch("core.news_reply.UniversalSearchModule", create=True) as ucls:
                    ucls.return_value.search = AsyncMock(side_effect=_univ)
                    with patch(
                        "modules.external_apis.clients.GenericSearchClient",
                        cls,
                    ):
                        with patch(
                            "core.universal_search_module.UniversalSearchModule",
                            ucls,
                        ):
                            return await _search_pack(
                                "test", country="", user_id="", timeout=8.0, tag="news_item_x"
                            )

        pack = asyncio.run(run())
        self.assertTrue(pack.get("ok"))
        self.assertEqual(calls[0], "univ")
        self.assertNotIn("ddg", calls)

    def test_enrich_per_headline_fills_snippet(self):
        import asyncio
        from core.news_reply import _enrich_rss_items_per_headline

        row = {
            "index": 1,
            "title": "\u0411\u043e\u043b\u0435\u0435 300 \u0434\u0435\u0442\u0435\u0439 \u0411\u043b\u0430\u0433\u043e\u0432\u0435\u0449\u0435\u043d\u0441\u043a",
            "publisher": "Amur.life",
            "snippet": "",
        }
        snip = (
            "\u0414\u0435\u0442\u0438 \u0438\u0437 \u0411\u043b\u0430\u0433\u043e\u0432\u0435\u0449\u0435\u043d\u0441\u043a\u0430 "
            "\u043f\u043e\u0435\u0434\u0443\u0442 \u0432 \u041a\u0438\u0442\u0430\u0439 \u0434\u043b\u044f \u043e\u0431\u043c\u0435\u043d\u0430."
        )

        async def _pack(q, *, country="", user_id="", timeout=0.0, tag=""):
            return {
                "ok": True,
                "results": [
                    {
                        "title": row["title"],
                        "snippet": snip,
                        "url": "https://amur.life/news/children-exchange",
                    }
                ],
            }

        async def run():
            with patch("core.news_reply._search_pack", AsyncMock(side_effect=_pack)):
                return await _enrich_rss_items_per_headline([row], country="BY", user_id="u")

        out = asyncio.run(run())
        self.assertGreaterEqual(len(str(out[0].get("snippet") or "")), 20)
        self.assertIn("\u043e\u0431\u043c\u0435\u043d", str(out[0].get("snippet") or "").lower())

    def test_compose_search_detail_picks_one_segment_from_blob(self):
        from core.news_reply import _compose_search_detail

        title = "\u0411\u043e\u043b\u0435\u0435 300 \u0434\u0435\u0442\u0435\u0439 \u0438\u0437 \u0411\u043b\u0430\u0433\u043e\u0432\u0435\u0449\u0435\u043d\u0441\u043a\u0430 \u0438 \u0425\u044d\u0439\u0445\u044d"
        blob = (
            "\u0425\u0430\u0431\u0430\u0440\u043e\u0432\u0441\u043a\u0438\u0439 \u043a\u0440\u0430\u0439 \u0443\u043a\u0440\u0435\u043f\u043b\u044f\u0435\u0442 \u0441\u0432\u044f\u0437\u0438 \u0441 \u041a\u0438\u0442\u0430\u0435\u043c - \u0420\u0443\u0441\u0441\u043a\u0438\u0439 \u0432\u0435\u043a; "
            f"{title} - Amur.life; "
            "\u0413\u0430\u0440\u0440\u0438 \u041a\u0430\u0441\u043f\u0430\u0440\u043e\u0432 \u0432 \u0440\u043e\u0437\u044b\u0441\u043a\u0435 - \u0412\u043e\u0442 \u0422\u0430\u043a"
        )
        out = _compose_search_detail(
            {"ok": True, "summary": blob},
            title=title,
            publisher="Amur.life",
        )
        self.assertIn("\u0411\u043b\u0430\u0433\u043e\u0432\u0435\u0449\u0435\u043d\u0441\u043a", out)
        self.assertNotIn("\u041a\u0430\u0441\u043f\u0430\u0440\u043e\u0432", out)
        self.assertLess(out.count(";"), 1)

    def test_consent_wall_rejected_for_item_pick(self):
        import asyncio
        from core.news_reply import (
            _fetch_news_item_detail,
            _looks_like_consent_wall,
            _sanitize_item_detail,
        )

        title = (
            "Российские и китайские соцсети: какие новости об Амурской области "
            "читают пользователи интернета"
        )
        consent = (
            "Важная информация RU Русский Deutsch English Español Français Italiano "
            "Все языки Afrikaans azərbaycan bosanski català Čeština Cymraeg Dansk Deutsch "
            "eesti English United Kingdom English United States Español España "
            "Войти в аккаунт RU Русский Deutsch English"
        )
        self.assertTrue(_looks_like_consent_wall(consent))
        self.assertEqual(_sanitize_item_detail(consent, title, publisher="Амурская правда"), "")

        item = {"index": 1, "title": title, "publisher": "Амурская правда", "snippet": ""}

        async def _search(*_a, **_k):
            return {"ok": True, "summary": consent, "results": []}

        async def _fetch(url, *, user_id="", title="", timeout=0.0):
            return consent

        async def run():
            with patch("core.news_reply._search_pack", AsyncMock(side_effect=_search)):
                with patch("core.news_reply._fetch_page_text", AsyncMock(side_effect=_fetch)):
                    with patch(
                        "core.news_reply._resolve_rss_row_for_item",
                        AsyncMock(return_value=None),
                    ):
                        with patch(
                            "core.news_reply._lookup_aggregate_detail",
                            AsyncMock(return_value=""),
                        ):
                            with patch(
                                "core.news_reply._llm_expand_news_item",
                                AsyncMock(
                                    return_value="Кратко: в соцсетях обсуждают темы региона."
                                ),
                            ):
                                return await _fetch_news_item_detail(
                                    item, user_id="u", country="RU", recent_dialogue=[]
                                )

        body = asyncio.run(run())
        self.assertNotIn("Все языки", body)
        self.assertNotIn("Важная информация", body)
        self.assertIn("соцсетях", body.lower())

    def test_portal_nav_finam_rejected(self):
        from core.news_reply import (
            _format_item_detail_reply,
            _item_has_digest_paragraph,
            _looks_like_portal_nav_blob,
        )

        finam = (
            "Мировые рынки снижаются на фоне нового витка — Финам.Ру. "
            "Утренний обзор Новости компаний и экономики Дивиденды "
            "Новости международных рынков Криптоновости"
        )
        self.assertTrue(_looks_like_portal_nav_blob(finam))
        title5 = (
            "Мировые рынки обвалились на фоне эскалации между Ираном и Израилем: "
            "S&P 500 упал на 2,1%, нефть подорожала на 4%. Инвесторы уходят в золото и доллар."
        )
        item = {"index": 5, "title": title5, "publisher": "", "snippet": ""}
        self.assertTrue(_item_has_digest_paragraph(item))
        out = _format_item_detail_reply(
            5,
            item,
            {"text": title5 + "\n" + finam, "images": [], "url": "", "truncated": False},
        )
        self.assertNotIn("Утренний обзор", out)
        self.assertNotIn("Криптоновости", out)
        self.assertIn("S&P 500", out)

    def test_homepage_chrome_rejected(self):
        from core.news_reply import (
            _looks_like_homepage_chrome,
            _page_text_usable,
            _text_relevant_to_title,
            _url_looks_like_article,
        )

        title = "Более 300 детей из Благовещенска и Хэйхэ поучаствуют в международных обменах"
        chrome = (
            "AMUR.LIFE Новости Люди Бизнес Что будем искать? Прайс-лист Медиакит "
            "Новости вчера Новости вчера Новости вчера"
        )
        self.assertTrue(_looks_like_homepage_chrome(chrome))
        self.assertFalse(_page_text_usable(chrome, title))
        self.assertFalse(_text_relevant_to_title(title, chrome))
        self.assertFalse(_url_looks_like_article("https://amur.life/"))
        self.assertTrue(
            _url_looks_like_article(
                "https://amur.life/news/2026/05/23/deti-blagoveshchensk-heihe-obmen/"
            )
        )

    def test_narrative_digest_format(self):
        from core.news_reply import _compose_digest_reply

        displayed = [
            {"index": 1, "title": "Тестовая новость A", "publisher": "Example"},
            {"index": 2, "title": "Тестовая новость B", "publisher": "Example"},
        ]
        narr = (
            "Сейчас в ленте чаще всего всплывают две линии: первая про тестовую новость A, "
            "вторая — про B. Это скорее обзор заголовков, без деталей из статей."
        )

        async def run():
            with patch.dict(
                os.environ,
                {
                    "NEWS_DIGEST_FORMAT": "narrative",
                    "NEWS_DIGEST_LLM_SUMMARY": "true",
                    "NEWS_RSS_FALLBACK_ENABLED": "true",
                },
                clear=False,
            ):
                with patch("core.news_reply._news_digest_llm_enabled", return_value=True):
                    with patch(
                        "core.news_reply._llm_digest_narrative_brief",
                        AsyncMock(return_value=narr),
                    ):
                        return await _compose_digest_reply(
                            displayed, user_query="что нового", user_id="u1"
                        )

        import asyncio

        out = asyncio.run(run())
        self.assertIn("ленте", out.lower())
        self.assertNotRegex(out, r"(?m)^\s*1\.\s+Тестовая")
        self.assertNotIn("Напишите номер пункта", out)


NARRATIVE_DIGEST = """Российская ПВО за ночь уничтожила 208 украинских беспилотников. Трамп и Конгресс пока не ответили на письмо Зеленского.
Беспилотник врезался в жилой дом в Румынии. Путин прокомментировал инцидент, вызвавший международный резонанс.
Польша решила лишить Зеленского ордена Белого Орла из-за почитания нацистов на Украине.
По данным Bloomberg, Иран нанёс ракетный удар по авиабазе США в Кувейте."""

NARRATIVE_RECENT = [
    {"role": "user", "text": "какие новости в мире"},
    {"role": "assistant", "text": NARRATIVE_DIGEST},
]


class NewsNarrativePerItemTests(unittest.TestCase):
    def test_narrative_digest_body_detected(self):
        self.assertTrue(_body_looks_like_news_digest(NARRATIVE_DIGEST))

    def test_story_deep_followup_after_narrative(self):
        self.assertTrue(
            looks_like_news_story_deep_followup(
                "расскажи про беспилотник который врезался в дом в Румынии",
                NARRATIVE_RECENT,
            )
        )
        self.assertFalse(looks_like_news_story_deep_followup("1", NARRATIVE_RECENT))

    def test_extract_story_query(self):
        q = _extract_story_search_query(
            "расскажи про беспилотник который врезался в дом"
        )
        self.assertIn("беспилотник", q.lower())

    def test_match_digest_paragraph_russia_drone(self):
        items = [
            {"index": 1, "title": "ПВО сбила 208 дронов", "publisher": "Example"},
            {"index": 2, "title": "Иран ударил по базе", "publisher": "Bloomberg"},
        ]
        row = _match_digest_item_by_user_query(
            "расскажи про беспилотник врезался в жилой дом в Румынии",
            items,
            NARRATIVE_DIGEST,
        )
        self.assertIsNotNone(row)
        self.assertIn("румын", (row.get("title") or row.get("snippet") or "").lower())

    def test_narrative_style_default_per_item(self):
        with patch.dict(os.environ, {}, clear=False):
            self.assertEqual(_news_digest_narrative_style(), "per_item")


if __name__ == "__main__":
    unittest.main()
