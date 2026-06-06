import unittest
import os
from unittest.mock import patch

from core.intent_heuristics import (
    explicit_math_request,
    is_system_operator_directive,
    is_utc_gmt_offset_only_message,
    looks_like_structured_multistep_instruction,
    math_route_is_ambiguous,
    merge_routing_prefs_from_turn,
    naive_math_intent_from_text,
    prose_narrative_disfavors_calculator,
    strip_urls_and_mentions_for_math_probe,
    user_asked_disable_calculator_router,
)


class TestIntentHeuristics(unittest.TestCase):
    def test_telegram_invite_not_math(self):
        url = "https://t.me/+u1CE7jW79-M2ZjUy"
        self.assertEqual(strip_urls_and_mentions_for_math_probe(url), "")
        self.assertFalse(naive_math_intent_from_text(url))

    def test_plain_math_still_math(self):
        self.assertTrue(naive_math_intent_from_text("сколько будет 2+2"))
        self.assertTrue(explicit_math_request("посчитай 3*4", "посчитай 3*4"))

    def test_utc_gmt_offset_not_math(self):
        self.assertFalse(naive_math_intent_from_text("UTC+3"))
        self.assertFalse(naive_math_intent_from_text("utc -5"))
        self.assertFalse(naive_math_intent_from_text("GMT+03:00"))
        self.assertFalse(naive_math_intent_from_text("(UTC+3)"))
        self.assertTrue(is_utc_gmt_offset_only_message("UTC+3"))
        self.assertFalse(is_utc_gmt_offset_only_message("сколько будет 2+2"))
        self.assertFalse(is_utc_gmt_offset_only_message("Europe/Moscow"))

    def test_math_with_url_suffix(self):
        self.assertTrue(naive_math_intent_from_text("реши 2+2 и вот ссылка https://t.me/x"))

    def test_codeword_letter_digit_hyphen_not_math(self):
        blob = "Запомни кодовое слово для теста: КРОКОДИЛ-774. Не объясняй почему."
        self.assertFalse(naive_math_intent_from_text(blob))
        self.assertFalse(explicit_math_request(blob, strip_urls_and_mentions_for_math_probe(blob)))

    def test_plus_prefix_number_latin_not_math(self):
        """+123 как международный/магический плюс, не «оператор +» вместе с цифрами."""
        blob = (
            "Привет 你好 latin +123 — проверка.\n"
            "‏הטקסט הזה RTL‏\n"
            "👍🏽 👨‍👩‍👧‍👦"
        )
        self.assertFalse(naive_math_intent_from_text(blob))
        self.assertFalse(explicit_math_request(blob, strip_urls_and_mentions_for_math_probe(blob)))

    def test_unicode_smoke_skin_hyphen_and_digits_not_math(self):
        """Составные слова со дефисом + цифры в другом месте (стресс-тест юникода) — не math."""
        blob = (
            "Привет 你好 مرحبا 🚀🎉 mixed РУС latin 123 — «кавычки» и длинное тире — проверка.\n\n"
            "‏הטקסט הזה RTL לבדיקה‏\n\n"
            "Смайлики и скин-тоны: 👍🏽 👨‍👩‍👧‍👦 (семья ZWJ)"
        )
        self.assertFalse(naive_math_intent_from_text(blob))
        self.assertFalse(explicit_math_request(blob, strip_urls_and_mentions_for_math_probe(blob)))

    def test_prose_slash_not_math_with_numbered_list(self):
        """Цитата с «1.» «2.» и «картинку/документ» не должна уходить в math."""
        blob = (
            '🧬 Хорошо. Вот пару вопросов для теста другого бота: 1. "Как ты обрабатываешь '
            "пересылаемые сообщения без контекста в групповом чате?\" 2. \"Если пользователь "
            'прислал тебе битую картинку/документ, как ты определишь"'
        )
        self.assertFalse(naive_math_intent_from_text(blob))

    def test_explicit_math_still_detected_with_prose_slash_elsewhere(self):
        self.assertTrue(explicit_math_request("реши 2+2 и ещё картинку/документ во вложении", None))

    def test_long_prose_embedded_sum_not_explicit_math(self):
        """Большой текст с «3 + 7» в середине без «посчитай» — не маршрут в калькулятор."""
        blob = (
            "Ниже длинное ТЗ для плагина. " * 25
            + "и вот пример 3 + 7 для иллюстрации, но это не задача. "
            + "Продолжаем описание " * 20
        )
        sc = strip_urls_and_mentions_for_math_probe(blob)
        self.assertTrue(naive_math_intent_from_text(blob))
        self.assertFalse(explicit_math_request(blob, sc))
        # Длинный текст без «посчитай» и т.п. — не дергаем уточнение про калькулятор.
        self.assertFalse(math_route_is_ambiguous(blob))

    def test_short_implicit_expression_still_math(self):
        self.assertTrue(explicit_math_request("  15*3  ", None))

    def test_long_prose_math_verb_without_arithmetic_not_explicit(self):
        """Длинный текст с «посчитай», но без /calc и без 2+2 — не math (ложные вызовы калькулятора)."""
        unit = (
            "Часть A. Внимательно посчитай, сколько раз встречается маркер в условии ниже. "
            "Игнорируй вложенные скобки. Часть B. Опиши FSM. "
        )
        blob = unit * 12
        self.assertGreater(len(blob), 400)
        sc = strip_urls_and_mentions_for_math_probe(blob)
        self.assertFalse(explicit_math_request(blob, sc))

    def test_long_prose_explicit_math_when_arithmetic_present(self):
        """Длинный текст, но есть явное «3+7» — посчитай всё ещё ведёт в math."""
        blob = ("Вводный абзац и вода. " * 20) + "посчитай 3+7 для проверки. " + ("ещё вода. " * 10)
        sc = strip_urls_and_mentions_for_math_probe(blob)
        self.assertTrue(explicit_math_request(blob, sc))

    def test_sensor_test_abcd_not_explicit_math(self):
        """Логический пакет TEST A/B/C/D с подсчётом символов не должен уходить в /calc."""
        blob = (
            "Отлично, тогда запускаю все четыре уровня A → B → C → D подряд.\n"
            "🟢 ТЕСТ A — МЯГКИЙ\n"
            "Символ: «л». Строка: Лампа льёт лунный луч.\n"
            "[TEST A] Позиции и символы. Итог: X\n"
            "🟡 ТЕСТ B — СРЕДНИЙ\n"
            "Символ: «е». Строка: Геолог изучает e-mail перед экспедицией.\n"
            "[TEST B] Позиции и символы (только кириллица). Итог: X\n"
            "🔴 ТЕСТ D — МАКСИМАЛЬНЫЙ\n"
            "Строка 1: позиции → X1. Строка 2: позиции → X2. Сравнение X1 и X2.\n"
            "Итог: OK / ERROR"
        )
        sc = strip_urls_and_mentions_for_math_probe(blob)
        self.assertFalse(explicit_math_request(blob, sc))

    def test_super_test_symbol_count_not_explicit_math(self):
        """Длинный многочастный тест с «посчитай количество символов #» — не модуль math."""
        blob = (
            "SUPER TEST 9000 — текст для вставки в бота\n"
            "Часть 1 — Символы и фильтры\n"
            "В строке ниже посчитай количество символов #, игнорируя всё внутри () и всё после @:\n"
            "###(##(#)##)####@##(#)#\n"
            "Часть 2 — FSM\n"
            "Есть состояния: INIT → READY → RUNNING → CLOSED.\n"
            "Часть 3 — Timeline\n"
            "10:00 пользователь отправил запрос 10:05 система ответила\n"
            "Часть 4 — Инструкции … Часть 5 — Самопроверка … Часть 6 — Финальный ответ"
        )
        sc = strip_urls_and_mentions_for_math_probe(blob)
        self.assertFalse(
            explicit_math_request(blob, sc),
            "подсчёт символов в строке не должен уводить в калькулятор",
        )

    def test_math_route_is_ambiguous_false_when_explicit(self):
        self.assertFalse(math_route_is_ambiguous("посчитай 3+7 в этом длинном тексте " * 5))

    def test_math_route_is_ambiguous_false_compact_implicit(self):
        self.assertFalse(math_route_is_ambiguous("15*3"))

    def test_recalculate_not_explicit_math_verb(self):
        """Подстрока calculate внутри recalculate не считается явным запросом."""
        self.assertFalse(explicit_math_request("Please recalculate the estimate tomorrow", None))

    def test_calculate_word_explicit_math(self):
        self.assertTrue(explicit_math_request("Please calculate 2+2", None))

    @patch.dict(os.environ, {"BRAIN_MATH_STRICT_MODE": "true"}, clear=False)
    def test_math_strict_mode_requires_calc(self):
        self.assertFalse(explicit_math_request("посчитай 2+2", None))
        self.assertTrue(explicit_math_request("/calc 2+2", None))

    def test_calculator_word_alone_not_explicit_math(self):
        blob = (
            "В отчете часто встречается слово калькулятор, формулы и расчеты, "
            "но это обсуждение качества и логов, а не просьба считать."
        )
        sc = strip_urls_and_mentions_for_math_probe(blob)
        self.assertFalse(explicit_math_request(blob, sc))

    def test_disable_calculator_phrase_never_forces_math(self):
        blob = "Пожалуйста, убери калькулятор, не суй /calc в длинные ответы 2+2 в тексте."
        sc = strip_urls_and_mentions_for_math_probe(blob)
        self.assertFalse(explicit_math_request(blob, sc))

    def test_routing_prefs_merge(self):
        rec: dict = {}
        merge_routing_prefs_from_turn(rec, "убери калькулятор, он не нужен")
        self.assertTrue((rec.get("routing_prefs") or {}).get("prefer_general_over_math"))
        merge_routing_prefs_from_turn(rec, "можно калькулятор")
        self.assertFalse((rec.get("routing_prefs") or {}).get("prefer_general_over_math"))

    def test_routing_prefs_merges_feedback_remarks(self):
        rec: dict = {}
        merge_routing_prefs_from_turn(rec, "не так, я имел в виду другое")
        rm = (rec.get("routing_prefs") or {}).get("recent_user_remarks") or []
        self.assertTrue(rm)

    def test_disable_phrases(self):
        self.assertTrue(user_asked_disable_calculator_router("\u043d\u0435 \u0441\u0443\u0439 \u043a\u0430\u043b\u044c\u043a\u0443\u043b\u044f\u0442\u043e\u0440"))

    def test_naprimer_not_explicit_math_verb(self):
        """«например:» не должно совпадать с триггером «пример:» для калькулятора."""
        blob = (
            "Длинная инструкция v.2.0: пункт 1 и 2, налог * 0.4 и 60%. "
            "например: см. таблицу ниже."
        )
        sc = strip_urls_and_mentions_for_math_probe(blob)
        self.assertTrue(naive_math_intent_from_text(blob))
        self.assertFalse(explicit_math_request(blob, sc))

    def test_system_directive_not_math(self):
        blob = (
            "СИСТЕМНАЯ ДИРЕКТИВА: РАБОТА НАД ОШИБКАМИ (v.2.0)\n"
            "1. Финансово-математический блок — налог * 0.4 для 60%.\n"
            "2. Блок логических итераций — таблица по дням.\n"
            "ТВОЙ НОВЫЙ АЛГОРИТМ ПРОВЕРКИ СЕБЯ: проценты и $."
        )
        sc = strip_urls_and_mentions_for_math_probe(blob)
        self.assertTrue(is_system_operator_directive(blob))
        self.assertFalse(naive_math_intent_from_text(blob))
        self.assertFalse(explicit_math_request(blob, sc))

    def test_system_directive_markers_without_header(self):
        blob = (
            "Документ с обязательные изменения и работа над ошибками. " * 3
            + "финансово-математический блок. блок логических итераций. "
            "сначала найди аномали. Проценты: 40% и коэффициент * 0.4."
        )
        self.assertTrue(is_system_operator_directive(blob))

    def test_facts_mem_dump_not_math_route(self):
        blob = (
            "📝 Ваши факты\nЗначения\n• country: Минске\n"
            "• interests: ['изучать программирование']\n• name: Алексей\n\nМета\n"
            "• country: {'updated_at': '2026-05-01T23:41:45.309019', "
            "'expires_at': '2027-05-01T23:41:45.309019', 'revoked': False, "
            "'source': 'message_extract', 'confidence': 0.9}\n"
        )
        self.assertFalse(naive_math_intent_from_text(blob))
        self.assertFalse(math_route_is_ambiguous(blob))

    def test_razreshilo_not_explicit_math_verb(self):
        """«разрешило» содержит подстроку «реши» — не считать это «реши уравнение»."""
        blob = (
            "Если отделение банка А разрешило снятие — добрать остаток (500-700 EUR). "
            "День 3-5: после снятия ограничений снять 800-1000 EUR кусками по лимиту."
        )
        sc = strip_urls_and_mentions_for_math_probe(blob)
        self.assertTrue(naive_math_intent_from_text(blob))
        self.assertFalse(explicit_math_request(blob, sc))

    def test_finance_word_problem_not_math_ambiguous(self):
        """Длинный сюжет с USD/налог/сценарий — не уводить в уточнение калькулятора."""
        blob = (
            "Бенчмарк B1. В начале дня 1 баланс 1000 USD. Каждый день баланс умножается на 2. "
            "В конце каждого чётного дня после удвоения налог 60% (остаётся 40%). "
            "Сделай таблицу для дней 1–5. Сценарий риска если банк ограничит переводы."
        )
        self.assertTrue(prose_narrative_disfavors_calculator(blob))
        sc = strip_urls_and_mentions_for_math_probe(blob)
        self.assertFalse(explicit_math_request(blob, sc))
        self.assertFalse(math_route_is_ambiguous(blob))

    def test_structured_multistep_instruction_not_math_ambiguous(self):
        blob = (
            "1) КОНТЕКСТ\n"
            "Скажи: «Я запомнил контекст» и кратко перескажи сообщение.\n\n"
            "2) РЕЗОННЫЙ ВЫВОД\n"
            "Есть 5 красных и 5 синих шаров, достаю 3. Найди вероятность хотя бы одного красного.\n\n"
            "3) МНОГОШАГОВОСТЬ\n"
            "- шаг 1: назови животное\n"
            "- шаг 2: придумай профессию\n"
            "- шаг 3: объясни почему\n\n"
            "4) ПАМЯТЬ\n"
            "Запомни: «Моё любимое число — 47». Потом скажи «Запомнил».\n\n"
            "6) ОШИБКОУСТОЙЧИВОСТЬ\n"
            "Ответь: 2 + два = ?\n"
        )
        self.assertTrue(looks_like_structured_multistep_instruction(blob))
        self.assertFalse(math_route_is_ambiguous(blob))


if __name__ == "__main__":
    unittest.main()
