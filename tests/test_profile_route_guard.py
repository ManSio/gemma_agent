"""Роутинг профиля: статьи/URL не должны уходить в math_solve; operational_diag — только на явный вопрос."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from core.brain.profile_route_guard import (
    clamp_profile,
    explicit_math_profile_allowed,
    extract_urls,
    is_url_only_message,
    looks_like_architecture_or_long_form_discussion,
    preflight_profile,
    text_mentions_article_context,
    url_looks_like_article,
)
from core.brain.text_helpers import is_bot_operational_diag_question


HABR = "https://habr.com/ru/companies/chestnyznak/articles/1037024/"
HABR_ARTICLE = "https://habr.com/ru/articles/1036002/"
GENERIC_URL = "https://example.com/page"
ARTICLE_PATH_URL = "https://example.com/blog/2026/05/30/story-title-here"

RAG_SNIPPET = (
    "Сейчас в проекте реализовано несколько идей из статьи про RAG-систему. "
    "experience_digest.jsonl и strategy_paths. "
    "Модуль reputation/ считает v_c для маршрутов. "
    "Qdrant для фактов, не для полнотекстового опыта. "
    "Скиллы math_reasoning и UrlFetch. "
    "Метрики RAGAS и golden_dataset. "
    "route_risk_cluster и auto-lessons. "
    "OpenRouter и API-ключ в .env.example — для админа, не для проверки баланса из чата. " * 3
)

# Зеркало кортежей в profile_route_guard.py (mutation: каждый маркер отдельно).
_ARTICLE_HOSTS = (
    "habr.com",
    "habr.ru",
    "medium.com",
    "vc.ru",
    "dev.to",
    "arxiv.org",
    "wikipedia.org",
    "github.io",
    "tproger.ru",
    "dou.ua",
)

_ARTICLE_TEXT_MARKERS = (
    "из статьи",
    "в статье",
    "по статье",
    "статья про",
    "статью про",
    "сравнительная таблица",
    "перескаж",
    "суммариз",
    "кратко перескаж",
    "что в тексте",
    "что в статье",
    "прочитай стать",
    "разбор стать",
)

_ARTICLE_PATH_URLS = (
    ("https://example.com/post/slug-one", True),
    ("https://example.com/blog/2026/entry", True),
    ("https://example.com/news/breaking", True),
    ("https://example.com/article/view", True),
    ("https://example.com/articles/list", True),
    ("https://example.com/companies/acme/articles/42", True),
    ("https://example.com/about/team", False),
)


def _pad_suffix(total_len: int, suffix: str) -> str:
    """Суффикс в конце (URL, хвост сообщения)."""
    gap = total_len - len(suffix)
    if gap < 0:
        raise ValueError(f"suffix longer than total_len={total_len}")
    return ("x" * gap) + suffix


def _pad_prefix(total_len: int, prefix: str) -> str:
    """Префикс в начале (команды перевода, приветствие)."""
    gap = total_len - len(prefix)
    if gap < 0:
        raise ValueError(f"prefix longer than total_len={total_len}")
    return prefix + ("x" * gap)


def _text_with_article_url(total_len: int, url: str = HABR_ARTICLE) -> str:
    return _pad_suffix(total_len, " " + url)


def _text_mixed_urls_len(total_len: int) -> str:
    """Habr + generic — не все URL «статья», граница len<120."""
    return _pad_suffix(total_len, f" {HABR_ARTICLE} {GENERIC_URL}")


class TestExtractUrls(unittest.TestCase):
    def test_extract_single_url(self):
        urls = extract_urls("Смотри https://habr.com/ru/articles/1/ и всё")
        self.assertEqual(len(urls), 1)
        self.assertIn("habr.com", urls[0])

    def test_extract_strips_trailing_punctuation(self):
        urls = extract_urls("(https://example.com/post/abc).")
        self.assertTrue(urls[0].endswith("abc"))

    def test_extract_empty(self):
        self.assertEqual(extract_urls(""), [])


class TestUrlOnlyMessage(unittest.TestCase):
    def test_url_only_true(self):
        self.assertTrue(is_url_only_message(HABR))

    def test_url_with_short_comment_false(self):
        self.assertFalse(
            is_url_only_message(f"{HABR} расскажи что там про RAG")
        )

    def test_no_url_false(self):
        self.assertFalse(is_url_only_message("просто текст без ссылки"))


class TestUrlLooksLikeArticle(unittest.TestCase):
    def test_habr_company_article(self):
        self.assertTrue(url_looks_like_article(HABR))

    def test_habr_article_path(self):
        self.assertTrue(url_looks_like_article(HABR_ARTICLE))

    def test_medium_host(self):
        self.assertTrue(url_looks_like_article("https://medium.com/@user/my-post-123"))

    def test_article_path_segment(self):
        self.assertTrue(url_looks_like_article(ARTICLE_PATH_URL))

    def test_generic_homepage_false(self):
        self.assertFalse(url_looks_like_article("https://example.com/"))

    def test_generic_landing_false(self):
        self.assertFalse(url_looks_like_article(GENERIC_URL))

    def test_arxiv_host(self):
        self.assertTrue(url_looks_like_article("https://arxiv.org/abs/2401.12345"))

    def test_vc_ru_host(self):
        self.assertTrue(url_looks_like_article("https://vc.ru/ai/123456-test"))

    def test_companies_articles_path(self):
        self.assertTrue(
            url_looks_like_article(
                "https://example.com/companies/foo/articles/bar-baz"
            )
        )


class TestTextMentionsArticle(unittest.TestCase):
    def test_from_article_marker(self):
        self.assertTrue(text_mentions_article_context("кратко перескажи из статьи про RAG"))

    def test_habr_prose_marker(self):
        self.assertTrue(
            text_mentions_article_context("на habr вышла статья про ботов")
        )

    def test_unrelated_false(self):
        self.assertFalse(text_mentions_article_context("какая погода завтра"))

    def test_summarize_marker(self):
        self.assertTrue(text_mentions_article_context("суммаризируй статью про ботов"))

    def test_read_article_marker(self):
        self.assertTrue(text_mentions_article_context("прочитай статью и ответь"))


class TestPhase1ArticleMarkers(unittest.TestCase):
    """Фаза 1: каждый хост/маркер/путь — отдельный subTest для mutmut."""

    def test_each_article_host_in_url(self):
        for host in _ARTICLE_HOSTS:
            with self.subTest(host=host):
                self.assertTrue(
                    url_looks_like_article(f"https://{host}/path/to/resource")
                )

    def test_each_article_path_pattern(self):
        for url, expected in _ARTICLE_PATH_URLS:
            with self.subTest(url=url):
                self.assertEqual(url_looks_like_article(url), expected)

    def test_each_article_text_marker(self):
        for marker in _ARTICLE_TEXT_MARKERS:
            with self.subTest(marker=marker):
                self.assertTrue(
                    text_mentions_article_context(f"нужно {marker} по теме сейчас")
                )

    def test_habr_prose_without_url(self):
        self.assertTrue(
            text_mentions_article_context(
                "на habr.com вышла статья о ботах без прямой ссылки"
            )
        )

    def test_extract_multiple_urls(self):
        txt = f"{HABR} и {GENERIC_URL}"
        urls = extract_urls(txt)
        self.assertEqual(len(urls), 2)

    def test_preflight_rewrite_sokrati_with_zametka(self):
        txt = "сократи этот фрагмент заметки: " + ("текст " * 22)
        self.assertGreater(len(txt), 80)
        self.assertEqual(preflight_profile(txt), "summarization")

    def test_preflight_rewrite_rezume_with_tekst(self):
        txt = "резюме документа с текстом: " + ("абзац " * 22)
        self.assertGreater(len(txt), 80)
        self.assertEqual(preflight_profile(txt), "summarization")

    def test_preflight_rewrite_kratkaya_versia(self):
        txt = "краткая версия заметки: " + ("строка " * 22)
        self.assertGreater(len(txt), 80)
        self.assertEqual(preflight_profile(txt), "summarization")

    def test_preflight_rewrite_sokhranyayushchiy_smysl(self):
        txt = "сохранив смысл перескажи заметку: " + ("фраза " * 18)
        self.assertGreater(len(txt), 80)
        self.assertEqual(preflight_profile(txt), "summarization")

    def test_preflight_rewrite_stati_keyword_short_len(self):
        txt = "сократи статью в журнале " + ("x" * 56)
        self.assertGreater(len(txt), 80)
        self.assertIn("стать", txt.lower())
        self.assertEqual(preflight_profile(txt), "summarization")


class TestArchitectureDiscussion(unittest.TestCase):
    def test_rag_paste_true(self):
        self.assertTrue(looks_like_architecture_or_long_form_discussion(RAG_SNIPPET))

    def test_short_text_false(self):
        self.assertFalse(
            looks_like_architecture_or_long_form_discussion("qdrant и rag в двух словах")
        )

    def test_single_marker_with_rag_in_text(self):
        body = (
            "Обсуждаем rag pipeline и qdrant retrieval в проекте. "
            + ("контекст проекта " * 22)
        )
        self.assertGreater(len(body), 350)
        self.assertTrue(looks_like_architecture_or_long_form_discussion(body))

    def test_two_markers_without_rag_phrase(self):
        body = (
            "В проекте есть experience_digest.jsonl и strategy_paths для маршрутов. "
            + ("детали архитектуры " * 24)
        )
        self.assertTrue(looks_like_architecture_or_long_form_discussion(body))

    def test_len_349_with_markers_false(self):
        body = (
            "qdrant и gemma_bot " + ("паддинг " * 40)
        )
        self.assertLess(len(body.strip()), 350)
        self.assertFalse(looks_like_architecture_or_long_form_discussion(body))

    def test_len_350_two_markers_true(self):
        body = _pad_prefix(
            350,
            "qdrant и gemma_bot в архитектуре",
        )
        self.assertEqual(len(body), 350)
        self.assertTrue(looks_like_architecture_or_long_form_discussion(body))

    def test_exactly_two_architecture_hits(self):
        body = (
            "модули experience_digest.jsonl и strategy_paths "
            + ("контекст " * 38)
        )
        self.assertGreaterEqual(len(body), 350)
        self.assertTrue(looks_like_architecture_or_long_form_discussion(body))


class TestExplicitMathAllowed(unittest.TestCase):
    def test_explicit_arithmetic_true(self):
        self.assertTrue(explicit_math_profile_allowed("посчитай 12 * (3+4)"))

    def test_long_prose_without_math_false(self):
        blob = "опиши архитектуру " + ("слово " * 80)
        self.assertFalse(explicit_math_profile_allowed(blob))


class TestPreflightProfile(unittest.TestCase):
    def test_empty_none(self):
        self.assertIsNone(preflight_profile(""))
        self.assertIsNone(preflight_profile("   "))

    def test_habr_url_only_summarization(self):
        self.assertEqual(preflight_profile(HABR), "summarization")

    def test_habr_with_context_summarization(self):
        self.assertEqual(preflight_profile(HABR_ARTICLE), "summarization")

    def test_short_message_with_article_url_summarization(self):
        short = f"читай {HABR_ARTICLE}"
        self.assertLess(len(short), 120)
        self.assertEqual(preflight_profile(short), "summarization")

    def test_architecture_paste_quick_explain(self):
        self.assertEqual(preflight_profile(RAG_SNIPPET), "quick_explain")

    def test_rewrite_note_summarization(self):
        txt = (
            "Перепиши заметку кратко, сохранив смысл.\n\n"
            "Заметка: как найти друзей\n"
            "Поиск друзей — это процесс..."
        )
        self.assertEqual(preflight_profile(txt), "summarization")

    def test_greeting_short(self):
        self.assertEqual(preflight_profile("Привет"), "short")

    def test_long_article_context_quick_explain(self):
        body = (
            "что в тексте про архитектуру microservices и observability: "
            + ("детали и контекст. " * 40)
        )
        self.assertGreater(len(body), 400)
        self.assertEqual(preflight_profile(body), "quick_explain")

    def test_very_long_plain_quick_explain(self):
        body = "опиши подход к проектированию " + ("слово " * 200)
        self.assertGreater(len(body), 900)
        self.assertEqual(preflight_profile(body), "quick_explain")

    def test_normal_question_none(self):
        self.assertIsNone(preflight_profile("сколько будет 2+2 если это не задача"))

    def test_generic_url_only_summarization(self):
        self.assertEqual(preflight_profile(GENERIC_URL), "summarization")

    def test_mixed_urls_not_all_article_no_forced_summarization(self):
        txt = (
            f"{HABR_ARTICLE} и ещё {GENERIC_URL} — что думаешь про оба источника? "
            + ("контекст " * 8)
        )
        self.assertGreater(len(txt), 120)
        self.assertIsNone(preflight_profile(txt))

    def test_long_article_url_without_mention_no_preflight(self):
        pad = "x" * 300
        txt = f"{pad} {HABR_ARTICLE}"
        self.assertGreater(len(txt), 320)
        self.assertIsNone(preflight_profile(txt))

    def test_short_habr_link_under_120_summarization(self):
        txt = f"смотри {HABR_ARTICLE}"
        self.assertLess(len(txt), 120)
        self.assertEqual(preflight_profile(txt), "summarization")

    def test_rewrite_short_without_note_none(self):
        self.assertIsNone(preflight_profile("кратко"))

    def test_greeting_too_long_not_short(self):
        txt = "привет, как у тебя дела сегодня и что нового в проекте"
        self.assertGreaterEqual(len(txt), 48)
        self.assertIsNone(preflight_profile(txt))

    @patch("core.brain.profile_route_guard.explicit_math_profile_allowed", return_value=True)
    def test_long_article_with_explicit_math_not_quick_explain(self, _mock_math):
        body = (
            "что в статье про интегралы: "
            + ("решить пример " * 45)
        )
        self.assertGreater(len(body), 400)
        self.assertIsNone(preflight_profile(body))

    @patch("core.brain.profile_route_guard.explicit_math_profile_allowed", return_value=True)
    def test_very_long_with_explicit_math_none(self, _mock_math):
        body = "вычисли " + ("задача " * 200)
        self.assertGreater(len(body), 900)
        self.assertIsNone(preflight_profile(body))


class TestUrlOnlyBoundaries(unittest.TestCase):
    def test_empty_false(self):
        self.assertFalse(is_url_only_message(""))

    def test_url_plus_seven_chars_true(self):
        self.assertTrue(is_url_only_message(f"{HABR} ok12345"))

    def test_url_plus_eight_chars_false(self):
        self.assertFalse(is_url_only_message(f"{HABR} ok123456"))

    def test_url_plus_comment_false(self):
        self.assertFalse(is_url_only_message(f"{HABR} расскажи подробнее про текст"))


class TestPhase2PreflightBoundaries(unittest.TestCase):
    """Фаза 2: пороги длины preflight_profile."""

    def test_article_urls_len_319_320_321(self):
        for length, expected in (
            (319, "summarization"),
            (320, None),
            (321, None),
        ):
            with self.subTest(length=length):
                txt = _text_with_article_url(length)
                self.assertEqual(len(txt), length)
                self.assertEqual(preflight_profile(txt), expected)

    def test_short_mixed_urls_len_118_119_120(self):
        for length, expected in (
            (118, "summarization"),
            (119, "summarization"),
            (120, None),
        ):
            with self.subTest(length=length):
                txt = _text_mixed_urls_len(length)
                self.assertEqual(len(txt), length)
                self.assertEqual(preflight_profile(txt), expected)

    def test_chitchat_len_47_48_49(self):
        self.assertEqual(preflight_profile("привет"), "short")
        txt48 = _pad_prefix(48, "привет")
        self.assertEqual(len(txt48), 48)
        self.assertIsNone(preflight_profile(txt48))
        txt49 = _pad_prefix(49, "привет")
        self.assertEqual(len(txt49), 49)
        self.assertIsNone(preflight_profile(txt49))

    def test_rewrite_len_80_81_without_zametka(self):
        short80 = _pad_prefix(80, "сократи ")
        self.assertEqual(len(short80), 80)
        self.assertIsNone(preflight_profile(short80))
        long81 = _pad_prefix(81, "сократи z")
        self.assertEqual(len(long81), 81)
        self.assertEqual(preflight_profile(long81), "summarization")

    def test_article_context_len_400_401(self):
        base = "что в статье написано про тему "
        short400 = _pad_prefix(400, base)
        self.assertEqual(len(short400), 400)
        self.assertIsNone(preflight_profile(short400))
        long401 = _pad_prefix(401, base + "x")
        self.assertEqual(len(long401), 401)
        self.assertEqual(preflight_profile(long401), "quick_explain")

    def test_plain_len_900_901(self):
        base = "опиши подход к проектированию "
        at900 = _pad_prefix(900, base)
        self.assertEqual(len(at900), 900)
        self.assertIsNone(preflight_profile(at900))
        at901 = _pad_prefix(901, base + "x")
        self.assertEqual(len(at901), 901)
        self.assertEqual(preflight_profile(at901), "quick_explain")


class TestPhase2ClampBoundaries(unittest.TestCase):
    """Фаза 2: пороги длины clamp_profile."""

    def test_translation_len_180_181_400_401(self):
        prefix = "текст без команды перевода "
        at180 = _pad_prefix(180, prefix)
        self.assertEqual(len(at180), 180)
        self.assertEqual(clamp_profile("translation", at180), "translation")
        at181 = _pad_prefix(181, prefix + "x")
        self.assertEqual(len(at181), 181)
        self.assertEqual(clamp_profile("translation", at181), "standard")
        at400 = _pad_prefix(400, prefix)
        self.assertEqual(len(at400), 400)
        self.assertEqual(clamp_profile("translation", at400), "standard")
        at401 = _pad_prefix(401, prefix + "x")
        self.assertEqual(len(at401), 401)
        self.assertEqual(clamp_profile("translation", at401), "quick_explain")

    def test_translation_perevod_prefix_passthrough(self):
        body = _pad_prefix(250, "перевод с русского ")
        self.assertGreater(len(body), 180)
        self.assertEqual(clamp_profile("translation", body), "translation")

    def test_math_never_on_article_len_80_81(self):
        short80 = _pad_prefix(80, "опиши контекст задачи")
        self.assertEqual(len(short80), 80)
        self.assertEqual(
            clamp_profile("math_solve", short80, router_confidence=0.5),
            "math_solve",
        )
        long81 = _pad_prefix(81, "опиши контекст задачи x")
        self.assertEqual(len(long81), 81)
        self.assertEqual(
            clamp_profile("math_solve", long81, router_confidence=0.5),
            "quick_explain",
        )

    def test_never_on_article_context_len_200_201_data_analysis(self):
        base = "что в статье написано "
        at200 = _pad_prefix(200, base)
        self.assertEqual(len(at200), 200)
        self.assertEqual(clamp_profile("data_analysis", at200), "data_analysis")
        at201 = _pad_prefix(201, base + "x")
        self.assertEqual(len(at201), 201)
        self.assertEqual(clamp_profile("data_analysis", at201), "quick_explain")

    def test_math_high_confidence_len_60_61_no_url(self):
        base = "что в статье написано "
        at60 = _pad_prefix(60, base)
        self.assertEqual(len(at60), 60)
        self.assertEqual(
            clamp_profile("math_solve", at60, router_confidence=0.95),
            "math_solve",
        )
        at61 = _pad_prefix(61, base + "x")
        self.assertEqual(len(at61), 61)
        self.assertEqual(
            clamp_profile("math_solve", at61, router_confidence=0.95),
            "quick_explain",
        )

    def test_math_high_confidence_with_url_summarization(self):
        self.assertEqual(
            clamp_profile("math_solve", HABR, router_confidence=0.95),
            "summarization",
        )


class TestClampProfile(unittest.TestCase):
    def test_habr_math_to_summarization(self):
        self.assertEqual(
            clamp_profile("math_solve", HABR, router_confidence=0.98),
            "summarization",
        )

    def test_rag_math_to_quick_explain(self):
        self.assertEqual(
            clamp_profile("math_solve", RAG_SNIPPET, router_confidence=0.98),
            "quick_explain",
        )

    def test_preflight_wins_over_router(self):
        self.assertEqual(clamp_profile("math_solve", "Привет"), "short")

    def test_invalid_profile_normalized(self):
        self.assertEqual(clamp_profile("not_a_real_profile", "тест"), "standard")

    def test_math_high_confidence_with_url_summarization(self):
        self.assertEqual(
            clamp_profile("math_solve", HABR, router_confidence=0.95),
            "summarization",
        )

    def test_math_long_prose_without_explicit_math_quick_explain(self):
        body = "что в статье написано про стоматологию " + ("и детали. " * 35)
        self.assertGreater(len(body), 200)
        self.assertTrue(text_mentions_article_context(body))
        self.assertEqual(clamp_profile("math_solve", body), "quick_explain")

    def test_code_debug_medical_quick_explain(self):
        txt = "после лечения пульпита осталась ошибка в прикусе, зуб болит"
        self.assertEqual(clamp_profile("code_debug", txt), "quick_explain")

    def test_code_generation_greeting_short(self):
        self.assertEqual(clamp_profile("code_generation", "привет"), "short")

    def test_code_generation_rewrite_summarization(self):
        txt = "Перепиши заметку кратко, сохранив смысл.\n\nЗаметка: текст " + ("x" * 120)
        self.assertEqual(clamp_profile("code_generation", txt), "summarization")

    def test_translation_long_prose_not_translate_cmd(self):
        body = "это длинный текст без команды перевода " + ("и ещё слова " * 31)
        self.assertGreater(len(body), 400)
        self.assertEqual(clamp_profile("translation", body), "quick_explain")

    def test_legal_long_article_context_quick_explain(self):
        body = (
            "статья про изменения в законодательстве "
            + ("и подробности. " * 25)
        )
        self.assertGreater(len(body), 200)
        self.assertEqual(clamp_profile("legal", body), "quick_explain")

    def test_standard_passthrough(self):
        self.assertEqual(
            clamp_profile("standard", "краткий вопрос без особенностей"),
            "standard",
        )

    def test_data_analysis_on_habr_summarization(self):
        self.assertEqual(
            clamp_profile("data_analysis", HABR, router_confidence=0.5),
            "summarization",
        )

    def test_translation_on_habr_summarization(self):
        self.assertEqual(
            clamp_profile("translation", HABR, router_confidence=0.5),
            "summarization",
        )

    def test_translation_medium_length_standard(self):
        body = "это обычный текст без команды перевода " + ("и ещё " * 28)
        self.assertGreater(len(body), 180)
        self.assertLess(len(body), 400)
        self.assertEqual(clamp_profile("translation", body), "standard")

    def test_translation_with_translate_command_passthrough(self):
        body = "переведи на английский " + ("абзац " * 40)
        self.assertGreater(len(body), 180)
        self.assertEqual(clamp_profile("translation", body), "translation")

    @patch("core.heuristic_context_gate.should_run_shortcut")
    def test_legal_with_pravo_marker_passthrough(self, mock_gate):
        mock_gate.return_value = MagicMock(allowed=True)
        body = (
            "нужен разбор нормы\n"
            "закон РБ law.example.com статья 12 " + ("детали " * 35)
        )
        self.assertGreater(len(body), 200)
        self.assertFalse(text_mentions_article_context(body))
        self.assertEqual(clamp_profile("legal", body), "legal")

    def test_math_low_confidence_short_text_passthrough(self):
        body = "посчитай 12 * (3+4)"
        self.assertLessEqual(len(body), 80)
        self.assertEqual(
            clamp_profile("math_solve", body, router_confidence=0.5),
            "math_solve",
        )

    def test_math_never_on_article_len_over_80_quick_explain(self):
        body = "опиши контекст " + ("без ссылок " * 12)
        self.assertGreater(len(body), 80)
        self.assertEqual(
            clamp_profile("math_solve", body, router_confidence=0.5),
            "quick_explain",
        )

    def test_math_high_confidence_no_url_quick_explain(self):
        body = "что в статье написано " + ("про тему " * 20)
        self.assertGreater(len(body), 60)
        self.assertFalse(extract_urls(body))
        self.assertEqual(
            clamp_profile("math_solve", body, router_confidence=0.95),
            "quick_explain",
        )

    @patch("core.heuristic_context_gate.should_run_shortcut")
    def test_code_debug_allowed_when_gate_ok(self, mock_gate):
        mock_gate.return_value = MagicMock(allowed=True)
        txt = "исправь traceback в модуле auth handler"
        self.assertEqual(clamp_profile("code_debug", txt), "code_debug")

    @patch("core.brain.code_empty_recovery.user_requests_code", return_value=True)
    def test_code_generation_when_code_requested(self, _mock_code):
        txt = "напиши скрипт на python для парсинга csv"
        self.assertEqual(clamp_profile("code_generation", txt), "code_generation")

    def test_architecture_paste_translation_to_quick_explain(self):
        self.assertEqual(
            clamp_profile("translation", RAG_SNIPPET, router_confidence=0.5),
            "quick_explain",
        )


class TestPhase3MutationSurvivors(unittest.TestCase):
    """Фаза 3: оставшиеся survived (CI 67.4% / 89) — добить запас по score."""

    def test_arch_one_marker_with_rag_word(self):
        body = _pad_prefix(360, "обсуждаем rag в контуре gemma_bot ")
        self.assertTrue(looks_like_architecture_or_long_form_discussion(body))

    def test_arch_one_marker_v_statye(self):
        body = _pad_prefix(360, "в статье описан модуль qdrant ")
        self.assertTrue(looks_like_architecture_or_long_form_discussion(body))

    def test_arch_one_marker_iz_statyi(self):
        body = _pad_prefix(360, "из статьи про openrouter_provider и API ")
        self.assertTrue(looks_like_architecture_or_long_form_discussion(body))

    def test_arch_one_marker_urlfetch_only_false(self):
        body = _pad_prefix(360, "метрика urlfetch без второго якоря ")
        self.assertFalse(looks_like_architecture_or_long_form_discussion(body))

    def test_habr_prose_without_stati_false(self):
        self.assertFalse(
            text_mentions_article_context("на habr.com погода и новости дня")
        )

    def test_explicit_math_whitespace_false(self):
        self.assertFalse(explicit_math_profile_allowed(""))
        self.assertFalse(explicit_math_profile_allowed("   "))

    @patch("core.intent_heuristics.strip_urls_and_mentions_for_math_probe")
    def test_explicit_math_exception_returns_false(self, mock_strip):
        mock_strip.side_effect = RuntimeError("probe failed")
        self.assertFalse(explicit_math_profile_allowed("посчитай 2+2"))

    def test_normalize_url_strips_bracket(self):
        urls = extract_urls("см. (https://example.com/post/x).")
        self.assertEqual(urls[0], "https://example.com/post/x")

    def test_is_url_only_two_article_urls(self):
        self.assertTrue(is_url_only_message(f"{HABR} {HABR_ARTICLE}"))

    def test_is_url_only_nine_char_remainder_false(self):
        self.assertFalse(is_url_only_message(f"{HABR} ok123456"))

    def test_url_looks_company_path_without_host_marker(self):
        self.assertTrue(
            url_looks_like_article(
                "https://cdn.example.org/companies/zn/articles/99-slug"
            )
        )

    def test_preflight_chitchat_variants_short(self):
        for greeting in ("спасибо", "пока", "hello", "hey"):
            with self.subTest(greeting=greeting):
                self.assertEqual(preflight_profile(greeting), "short")

    def test_preflight_article_mention_at_len_321_summarization(self):
        suffix = " что в статье " + HABR_ARTICLE
        txt = _pad_suffix(321, suffix)
        self.assertEqual(len(txt), 321)
        self.assertTrue(text_mentions_article_context(txt))
        self.assertEqual(preflight_profile(txt), "summarization")

    def test_preflight_rewrite_tekst_keyword_only(self):
        txt = _pad_prefix(90, "сократи документ с текстом внутри ")
        self.assertEqual(preflight_profile(txt), "summarization")

    def test_clamp_empty_profile_normalized(self):
        self.assertEqual(clamp_profile("", "любой текст"), "standard")

    def test_clamp_translation_newline_perevedi(self):
        body = _pad_prefix(220, "вступление\nпереведи на английский ")
        self.assertEqual(clamp_profile("translation", body), "translation")

    def test_clamp_translation_at_len_180_passthrough(self):
        body = _pad_prefix(180, "обычный текст ")
        self.assertEqual(clamp_profile("translation", body), "translation")

    @patch("core.heuristic_context_gate.should_run_shortcut")
    def test_clamp_legal_staya_len_201_quick_explain(self, mock_gate):
        mock_gate.return_value = MagicMock(allowed=True)
        base = "статья про налоговый кодекс "
        at201 = _pad_prefix(201, base)
        self.assertGreater(len(at201), 200)
        self.assertTrue(text_mentions_article_context(at201))
        self.assertEqual(clamp_profile("legal", at201), "quick_explain")

    @patch("core.heuristic_context_gate.should_run_shortcut")
    def test_clamp_legal_staya_len_200_passthrough(self, mock_gate):
        mock_gate.return_value = MagicMock(allowed=True)
        base = "статья 12 налогового кодекса "
        at200 = _pad_prefix(200, base)
        self.assertEqual(len(at200), 200)
        self.assertEqual(clamp_profile("legal", at200), "legal")

    @patch("core.brain.code_empty_recovery.user_requests_code", return_value=False)
    def test_clamp_code_generation_without_code_request(self, _mock):
        txt = "напиши обзор идеи без исполняемого кода " + ("абзац " * 12)
        self.assertEqual(clamp_profile("code_generation", txt), "quick_explain")

    @patch("core.brain.code_empty_recovery.user_requests_code", return_value=True)
    def test_clamp_code_generation_with_kod_stays_generation(self, _mock):
        txt = "улучши код модуля на python " + ("строка " * 20)
        self.assertIn("код", txt.lower())
        self.assertEqual(clamp_profile("code_generation", txt), "code_generation")

    @patch("core.heuristic_context_gate.should_run_shortcut")
    def test_clamp_gate_exception_keeps_profile(self, mock_gate):
        mock_gate.side_effect = RuntimeError("gate down")
        txt = "исправь traceback в модуле auth"
        self.assertEqual(clamp_profile("code_debug", txt), "code_debug")

    def test_clamp_math_confidence_089_no_high_confidence_path(self):
        body = _pad_prefix(70, "что в статье написано ")
        self.assertEqual(
            clamp_profile("math_solve", body, router_confidence=0.89),
            "math_solve",
        )

    def test_clamp_math_confidence_090_quick_explain(self):
        body = _pad_prefix(70, "что в статье написано ")
        self.assertEqual(
            clamp_profile("math_solve", body, router_confidence=0.9),
            "quick_explain",
        )

    def test_clamp_math_exactly_80_chars_no_clamp(self):
        body = _pad_prefix(80, "вычисли сумму")
        self.assertEqual(
            clamp_profile("math_solve", body, router_confidence=0.5),
            "math_solve",
        )

    def test_each_architecture_marker_pair_true(self):
        markers = (
            "experience_digest",
            "strategy_paths",
            "ragas",
            "urlfetch",
            "route_risk_cluster",
            "reputation/",
            "math_reasoning",
            "openrouter_provider",
            "микро-rag",
        )
        for marker in markers:
            with self.subTest(marker=marker):
                body = _pad_prefix(
                    360,
                    f"модули {marker} и gemma_bot в архитектуре ",
                )
                self.assertTrue(looks_like_architecture_or_long_form_discussion(body))


class TestProfileRouteGuardContract(unittest.TestCase):
    """Контрактные пары: смена ветки должна менять профиль (ловит мутации >= / and / or)."""

    def test_preflight_boundary_319_vs_321_article_urls(self):
        pad319 = "z" * (319 - len(HABR_ARTICLE) - 1)
        short_block = f"{pad319} {HABR_ARTICLE}"
        self.assertLess(len(short_block), 320)
        self.assertEqual(preflight_profile(short_block), "summarization")

        pad_long = "z" * 400
        long_block = f"{pad_long} {HABR_ARTICLE}"
        self.assertGreater(len(long_block), 320)
        self.assertIsNone(preflight_profile(long_block))

    def test_clamp_translation_len_401_vs_399(self):
        long401 = "текст без команды перевода " + ("слово " * 90)
        self.assertGreater(len(long401), 400)
        self.assertEqual(clamp_profile("translation", long401), "quick_explain")

        mid250 = "текст без команды перевода " + ("слово " * 30)
        self.assertGreater(len(mid250), 180)
        self.assertLess(len(mid250), 400)
        self.assertEqual(clamp_profile("translation", mid250), "standard")


class TestOperationalDiag(unittest.TestCase):
    def test_not_on_rag_paste(self):
        self.assertFalse(is_bot_operational_diag_question(RAG_SNIPPET))

    def test_on_balance_question(self):
        self.assertTrue(
            is_bot_operational_diag_question("проверь баланс openrouter и ключ api")
        )


class TestProfileRouteGuardLegacy(unittest.TestCase):
    """Сохраняем имена старых тестов для grep/ACC."""

    def test_habr_url_preflight_summarization(self):
        self.assertTrue(url_looks_like_article(HABR))
        self.assertTrue(is_url_only_message(HABR))
        self.assertEqual(preflight_profile(HABR), "summarization")

    def test_greeting_no_forced_profile(self):
        self.assertIsNone(preflight_profile("Приветик"))
        self.assertFalse(is_bot_operational_diag_question("Приветик"))


if __name__ == "__main__":
    unittest.main()
