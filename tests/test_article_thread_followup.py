"""Продолжение темы статьи: «что ещё известно»."""
from __future__ import annotations

import unittest

import asyncio

from core.article_thread_followup import (
    build_thread_search_query,
    extract_article_thread_subject,
    format_article_thread_followup_from_items,
    looks_like_article_thread_followup,
    looks_like_news_digest_leak,
    sanitize_article_thread_direct_reply,
    should_handle_article_thread_followup,
    _rank_items_for_subject,
)
from core.dialogue_slots import SLOT_ARTICLE_THREAD, set_slot, user_refers_to_article_thread
from core.incident_context_hint import build_incident_context_hint, try_incident_followup_search_reply


class ArticleThreadFollowupTests(unittest.TestCase):
    def test_phrase_detected(self) -> None:
        self.assertTrue(looks_like_article_thread_followup("Что ещё известно"))
        self.assertTrue(looks_like_article_thread_followup("что еще известно?"))
        self.assertTrue(looks_like_article_thread_followup("что еще слышноэ"))
        self.assertTrue(looks_like_article_thread_followup("что ещё по этой теме"))
        self.assertTrue(looks_like_article_thread_followup("подробние"))
        self.assertFalse(looks_like_article_thread_followup("расскажи анекдот про котов"))
        self.assertFalse(
            looks_like_article_thread_followup(
                "какие сегодня главные новости в мире и что важного произошло"
            )
        )

    def test_trump_paste_slyshno_blocks_and_handles(self) -> None:
        """Регрессия Example Bot: «что еще слышно» после paste не уходит в общий digest."""
        from core.article_thread_followup import article_followup_blocks_news_digest

        paste = (
            "🖼 Axios: Дональд Трамп требует внести изменения в проект соглашения с Ираном\n\n"
            "По данным портала, президент США на совещании попросил внести правки. "
            "Ормузский пролив и утилизация урана."
        )
        dlg = [
            {"role": "user", "text": paste},
            {
                "role": "assistant",
                "text": "Трамп потребовал правки в проект соглашения с Ираном. Что известно ещё?",
            },
        ]
        phrase = "что еще слышноэ"
        self.assertTrue(looks_like_article_thread_followup(phrase))
        self.assertTrue(should_handle_article_thread_followup(phrase, dlg))
        self.assertTrue(article_followup_blocks_news_digest(phrase, dlg))

    def test_blocks_news_aligned_with_should_handle_on_paste(self) -> None:
        from core.article_thread_followup import article_followup_blocks_news_digest

        paste = "В Крыму ввели талоны на бензин. " * 10
        dlg = [
            {"role": "user", "text": paste},
            {"role": "assistant", "text": "Кратко про талоны в Крыму."},
        ]
        for phrase in ("подробнее", "что еще слышно", "что ещё известно"):
            blocks = article_followup_blocks_news_digest(phrase, dlg)
            handles = should_handle_article_thread_followup(phrase, dlg)
            self.assertEqual(blocks, handles, msg=phrase)

    def test_numbered_list_without_article_header_is_digest_leak(self) -> None:
        leak = "Из других заметных событий:\n1. Иран\n2. ЗАЭС\n3. Крым"
        self.assertTrue(looks_like_news_digest_leak(leak))
        safe = "Дополнительно по теме\nТрамп и Иран: новый раунд контактов."
        self.assertFalse(looks_like_news_digest_leak(safe))

    def test_user_refers_with_paste_context(self) -> None:
        paste = (
            "Аэропорт Мюнхена закрыли на час из-за подозрительного объекта, "
            "похожего на беспилотник. " * 12
        )
        dlg = [
            {"role": "user", "text": paste},
            {"role": "assistant", "text": "Ранним утром аэропорт Мюнхена был закрыт…"},
        ]
        self.assertTrue(user_refers_to_article_thread("Что ещё известно", dlg))
        self.assertTrue(should_handle_article_thread_followup("Что ещё известно", dlg))

    def test_extract_subject_from_paste(self) -> None:
        paste = (
            "Аэропорт Мюнхена закрыли на час из-за подозрительного объекта, "
            "похожего на беспилотник."
        )
        dlg = [
            {"role": "user", "text": paste},
            {"role": "assistant", "text": "Краткий пересказ про закрытие аэропорта Мюнхена."},
        ]
        sub = extract_article_thread_subject(dlg)
        self.assertIsNotNone(sub)
        self.assertIn("Мюнхен", sub or "")

    def test_slot_topic(self) -> None:
        rec: dict = {}
        set_slot(
            rec,
            SLOT_ARTICLE_THREAD,
            {"topic": "Аэропорт Мюнхена закрыли на час"},
            turns=5,
        )
        sub = extract_article_thread_subject([], rec)
        self.assertIn("Мюнхен", sub or "")

    def test_focused_search_query(self) -> None:
        q = build_thread_search_query("Аэропорт Мюнхена закрыли из-за беспилотника")
        low = q.lower()
        self.assertTrue(
            "munich" in low or "мюнхен" in low or "airport" in low or "аэропорт" in low,
            msg=q,
        )
        self.assertNotIn("site:rbc.ru", q)

    def test_ukraine_drone_query_from_full_paste(self) -> None:
        paste = (
            "Ночью в ряде регионов Украины была объявлена воздушная тревога, "
            "сообщалось о взрывах в Харьковской, Черниговской областях. "
            "Воздушные силы ВСУ заявили о нейтрализации 212 из 229 обнаруженных беспилотников."
        )
        sub = extract_article_thread_subject(
            [{"role": "user", "text": paste}],
        )
        self.assertIn("212", sub or "")
        self.assertIn("Харьков", sub or "")
        q = build_thread_search_query(sub or paste)
        low = q.lower()
        self.assertIn("ukraine", low)
        self.assertTrue("kharkiv" in low or "chernihiv" in low or "drone" in low, msg=q)

    def test_ukraine_followup_filters_weather_and_russia_alert(self) -> None:
        subject = (
            "Харьковская Черниговская области 212 из 229 беспилотников воздушная тревога Украина"
        )
        items = [
            {
                "title": "В ряде регионов России ночью введены режимы беспилотной опасности",
                "snippet": "мониторинг",
                "publisher": "x",
            },
            {
                "title": "До конца суток грозы и град в ряде регионов",
                "snippet": "ukranews",
                "publisher": "ukranews.com",
            },
            {
                "title": "Харьковская область: повреждены здания после атаки БПЛА",
                "snippet": "ночная атака дронов",
                "publisher": "ria.ru",
            },
        ]
        ranked = _rank_items_for_subject(items, subject)
        self.assertEqual(len(ranked), 1)
        self.assertIn("Харьков", ranked[0].get("title", ""))

    def test_crimea_fuel_query(self) -> None:
        sub = "В Крыму ввели талоны на бензин АИ-95 и АИ-92"
        q = build_thread_search_query(sub)
        low = q.lower()
        self.assertTrue("crimea" in low or "sevastopol" in low or "fuel" in low, msg=q)

    def test_sanitize_blocks_digest_leak(self) -> None:
        leak = "Главные новости\n1. Что-то\n2. Другое\nНапишите номер пункта."
        self.assertTrue(looks_like_news_digest_leak(leak))
        out = sanitize_article_thread_direct_reply(leak)
        self.assertIn("мало нового", out.lower())

    def test_podrobnee_blocks_news_digest(self) -> None:
        from core.article_thread_followup import article_followup_blocks_news_digest
        from core.brain.text_helpers import wants_expanded_news_digest

        paste = (
            "В Крыму ввели талоны на бензин АИ-95 и АИ-92. " * 12
        )
        dlg = [
            {"role": "user", "text": paste},
            {"role": "assistant", "text": "Краткий пересказ про талоны на бензин в Крыму."},
        ]
        self.assertTrue(article_followup_blocks_news_digest("подробнее", dlg))
        self.assertFalse(wants_expanded_news_digest("подробнее", dlg))

    def test_offtopic_row_filtered(self) -> None:
        items = [
            {
                "title": "Wetransfert ne détecte pas mes fichiers",
                "snippet": "forums.commentcamarche.net",
                "publisher": "x",
            },
            {
                "title": "В Крыму лимит 20 литров АИ-92",
                "snippet": "талоны на АЗС TES",
                "publisher": "ria.ru",
            },
        ]
        body = format_article_thread_followup_from_items(
            items,
            subject="талоны бензин Крым",
        )
        self.assertIn("Крым", body)
        self.assertNotIn("Wetransfert", body)

    def test_followup_format_no_digest_footer(self) -> None:
        items = [
            {
                "title": "Топливный кризис в Крыму: талоны на бензин",
                "snippet": "Ограничения на АЗС TES и АТАН.",
                "publisher": "rfi.fr",
            },
            {
                "title": "Discord server news",
                "snippet": "unrelated",
                "publisher": "x.com",
            },
        ]
        body = format_article_thread_followup_from_items(
            items,
            subject="В Крыму ввели талоны на бензин",
        )
        self.assertIn("Дополнительно", body)
        self.assertNotIn("<b>", body)
        self.assertIn("Крым", body)
        self.assertNotIn("номер пункта", body.lower())
        self.assertNotIn("Главные новости", body)

    def test_incident_hint_empty_when_article_followup(self) -> None:
        paste = (
            "Аэропорт Мюнхена закрыли на час из-за подозрительного объекта, "
            "похожего на беспилотник. " * 8
        )
        dlg = [
            {"role": "user", "text": paste},
            {"role": "assistant", "text": "Краткий пересказ про закрытие аэропорта Мюнхена."},
        ]
        self.assertEqual(build_incident_context_hint("Что ещё известно", dlg), "")

    def test_incident_search_skipped_when_article_thread(self) -> None:
        paste = (
            "Аэропорт Мюнхена закрыли на час из-за подозрительного объекта, "
            "похожего на беспилотник. " * 8
        )
        dlg = [
            {"role": "user", "text": paste},
            {"role": "assistant", "text": "Краткий пересказ про закрытие аэропорта Мюнхена."},
        ]
        out = asyncio.run(
            try_incident_followup_search_reply("Что ещё известно", recent_dialogue=dlg)
        )
        self.assertIsNone(out)

    def test_search_query_no_generic_digest_fallback(self) -> None:
        q = build_thread_search_query("Аэропорт Мюнхена закрыли из-за беспилотника")
        self.assertNotIn("site:rbc.ru", q)
        self.assertNotIn("международные новости", q.lower())

    def test_news_story_deep_skipped_for_article_thread(self) -> None:
        paste = (
            "Аэропорт Мюнхена закрыли на час из-за подозрительного объекта, "
            "похожего на беспилотник. " * 8
        )
        dlg = [
            {"role": "user", "text": paste},
            {"role": "assistant", "text": "Краткий пересказ про закрытие аэропорта Мюнхена."},
        ]
        from core.news_reply import try_news_story_deep_reply

        out = asyncio.run(
            try_news_story_deep_reply("Что ещё известно", recent_dialogue=dlg)
        )
        self.assertIsNone(out)

    def test_pre_llm_article_thread_followup(self) -> None:
        paste = (
            "Аэропорт Мюнхена закрыли на час из-за подозрительного объекта, "
            "похожего на беспилотник. " * 8
        )
        dlg = [
            {"role": "user", "text": paste},
            {"role": "assistant", "text": "Краткий пересказ про закрытие аэропорта Мюнхена."},
        ]
        from core.dialogue_slots import SLOT_ARTICLE_THREAD, set_slot
        from core.pre_llm_plan import try_pre_llm_direct_plan

        rec: dict = {"recent_messages": dlg}
        set_slot(rec, SLOT_ARTICLE_THREAD, {"topic": "Аэропорт Мюнхена"}, turns=5)
        with unittest.mock.patch(
            "core.article_thread_followup.try_article_thread_followup_reply_sync",
            return_value="По теме аэропорта: …",
        ):
            got = try_pre_llm_direct_plan(
                user_id="1",
                group_id=None,
                text="Что ещё известно",
                persisted=rec,
                input_meta={},
            )
        self.assertIsNotNone(got)
        self.assertEqual(got[0], "article_thread_followup_nl")
        self.assertIn("аэропорт", got[1].lower())

    def test_sanitize_rejects_news_digest_leak(self) -> None:
        from core.article_thread_followup import (
            article_thread_honest_fallback_reply,
            looks_like_news_digest_leak,
            sanitize_article_thread_direct_reply,
        )

        leak = "Главные новости\n\n1. Крым …\n\nНапишите номер пункта"
        self.assertTrue(looks_like_news_digest_leak(leak))
        out = sanitize_article_thread_direct_reply(leak)
        self.assertEqual(out, article_thread_honest_fallback_reply())
        self.assertNotIn("Главные новости", out)

    def test_finalize_pre_llm_on_empty_reply(self) -> None:
        from core.article_thread_followup import finalize_article_thread_pre_llm_reply

        paste = "Аэропорт Мюнхена закрыли из-за беспилотника. " * 6
        dlg = [
            {"role": "user", "text": paste},
            {"role": "assistant", "text": "Кратко про Мюнхен."},
        ]
        body = finalize_article_thread_pre_llm_reply(
            "Что ещё известно",
            None,
            recent_dialogue=dlg,
            persisted={"recent_messages": dlg},
        )
        self.assertIn("мало нового", body.lower())
        self.assertNotIn("главные новости", body.lower())

    def test_gate_blocked_returns_honest_fallback(self) -> None:
        paste = "Аэропорт Мюнхена закрыли из-за беспилотника. " * 6
        dlg = [
            {"role": "user", "text": paste},
            {"role": "assistant", "text": "Кратко про Мюнхен."},
        ]

        class _Gate:
            allowed = False

        async def _blocked(*_a, **_k):
            return _Gate()

        with unittest.mock.patch(
            "core.heuristic_context_gate.should_run_shortcut_async",
            side_effect=_blocked,
        ):
            out = asyncio.run(
                __import__(
                    "core.article_thread_followup", fromlist=["try_article_thread_followup_reply"]
                ).try_article_thread_followup_reply(
                    "Что ещё известно",
                    recent_dialogue=dlg,
                    persisted={"recent_messages": dlg},
                )
            )
        self.assertIsNotNone(out)
        self.assertIn("мало нового", (out or "").lower())


if __name__ == "__main__":
    unittest.main()
