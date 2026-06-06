"""Unit tests for core.reform_probe_support (no LLM)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.reform_probe_support import (
    cleanup_probe_behavior,
    looks_truncated_reply,
    reply_blob,
    substantive_reply_text,
    validate_baseline_live_reply,
    validate_news_reply_no_rss_leak,
    validate_news_world_reply,
    validate_paste_article_reply,
    validate_pending_correction,
    validate_philosophy_reply,
    validate_weather_reply,
)


class TestReformProbeSupport(unittest.TestCase):
    def test_validate_weather_ok(self) -> None:
        self.assertEqual(
            validate_weather_reply("Погода — Минск, Беларусь: +5°C, ветер 3 м/с"),
            [],
        )

    def test_validate_weather_not_minsk(self) -> None:
        errs = validate_weather_reply("Погода: +5°C, ветер 3 м/с")
        self.assertIn("weather_not_minsk", errs)

    def test_baseline_empty_guard(self) -> None:
        errs = validate_baseline_live_reply(
            "Пустой ответ после обработки (фильтр ответа или модель). Повтори короче."
        )
        self.assertIn("empty_guard", errs)

    def test_baseline_country_confirm(self) -> None:
        self.assertIn(
            "country_confirm_leak",
            validate_baseline_live_reply("Запомнить страну? Ответь «да» или «нет»."),
        )

    def test_news_world_google_meta_only(self) -> None:
        blob = (
            "Мировые новости\n1. Google Новости - В мире\n"
            "   Читайте статьи в приложении Google Новости."
        )
        self.assertIn("news_google_meta_dump", validate_news_world_reply(blob))

    def test_paste_search_error(self) -> None:
        blob = "Произошла внутренняя ошибка поиска — запрос не был передан."
        self.assertIn("search_internal_error", validate_paste_article_reply(blob))

    def test_truncated_philosophy(self) -> None:
        long_cut = "Кант и Сартр " + ("свобода " * 30) + "механическая р"
        self.assertTrue(looks_truncated_reply(long_cut))

    def test_substantive_strips_correction_ack(self) -> None:
        raw = "📝 Учту вашу правку в следующих 6 ответах.\n\nНоль букв."
        self.assertIn("Ноль", substantive_reply_text(raw))
        self.assertNotIn("Учту", substantive_reply_text(raw))

    def test_validate_weather_ask_city(self) -> None:
        errs = validate_weather_reply("Напишите город")
        self.assertTrue(any("ask_city" in e for e in errs))

    def test_validate_philosophy_weather_leak(self) -> None:
        errs = validate_philosophy_reply("Погода в Минске сейчас +2°C")
        self.assertIn("philosophy_weather_leak", errs)

    def test_validate_news_rss_leak(self) -> None:
        errs = validate_news_reply_no_rss_leak("Смотрите Google News RSS")
        self.assertTrue(any("news_rss_leak" in e for e in errs))

    def test_validate_news_rss_leak_clean(self) -> None:
        errs = validate_news_reply_no_rss_leak("Заголовки из поиска: 1. Новость дня")
        self.assertEqual(errs, [])

    def test_pending_correction(self) -> None:
        rec = {
            "routing_prefs": {
                "pending_correction": {"instruction": "короче", "turns_left": 4},
            }
        }
        self.assertEqual(validate_pending_correction(rec), [])
        self.assertEqual(
            validate_pending_correction({"routing_prefs": {}}),
            ["correction:no_pending_correction"],
        )

    def test_reply_blob_no_duplicate_telegram_messages(self) -> None:
        blob = reply_blob(
            {
                "outputs": [{"type": "text", "payload": "Запомнил."}],
                "telegram_messages": ["Запомнил."],
            }
        )
        self.assertEqual(blob, "Запомнил.")

    def test_cleanup_probe_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bdir = root / "data" / "users" / "behavior"
            bdir.mkdir(parents=True)
            from tests.fixtures.telegram_test_ids import TEST_USER_UID

            p = bdir / f"{TEST_USER_UID}__dm.json"
            p.write_text("{}", encoding="utf-8")
            n = cleanup_probe_behavior(TEST_USER_UID, root=root)
            self.assertEqual(n, 1)
            self.assertFalse(p.is_file())


if __name__ == "__main__":
    unittest.main()
