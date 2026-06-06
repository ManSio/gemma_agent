"""Регрессия: anti_intrusion не должен подменять ответ шаблоном «следующий тезис» (см. багрепорт 20260505)."""

import unittest

from core.input_layer import (
    _apply_anti_intrusion_guard,
    _is_intrusive_service_reply,
    _looks_like_long_prose_discussion,
)
from core.models import Output


_LONG_BANK_SCENARIO_USER = """Понял, продолжаю. Давай разложу по дням конкретные шаги для каждого сценария — без привязки к конкретной валюте, но в логике EUR (раз фигурировали в условии). Если базовая валюта другая — просто подставь свою.

День 1–2: разведка и первая линия
- Позвонить в банк А (горячая линия или чат). Три вопроса:
  • «Техработы» — это только переводы в валюте или снятие наличных в банкомате тоже временно недоступно?
  • До какой даты/часа действует ограничение?
  • Какой суточный лимит на снятие наличных по карте (EUR-эквивалент) — обычно 500–1000.
- Написать в отель (через Booking/email/чат): «Я планирую оплатить депозит картой, но возможны задержки. Могу ли я внести 200 EUR наличными при заселении или оплатить другой картой на месте?». Ответ сохранить скриншотом.

Повторный вопрос про базовую валюту (можно ответить цифрой и кодом):

Какую валюту считаешь основной в своём финансовом плане — EUR, USD, BYN, RUB или другую? Это поможет точнее прикинуть лимиты и комиссии."""

_CURRENCY_NAG = "Уточни, пожалуйста, базовую валюту (ISO код, например USD, EUR или BYN)."


class AntiIntrusionGuardTests(unittest.TestCase):
    def test_long_prose_user_triggers_guard_context(self):
        self.assertTrue(_looks_like_long_prose_discussion(_LONG_BANK_SCENARIO_USER))

    def test_currency_nag_removed_when_main_reply_exists(self):
        main = Output(type="text", payload="Основной ответ про банк и поездку.", meta={})
        nag = Output(type="text", payload=_CURRENCY_NAG, meta={})
        kept, silent = _apply_anti_intrusion_guard(_LONG_BANK_SCENARIO_USER, [main, nag])
        self.assertEqual(kept, [main])
        self.assertFalse(silent)

    def test_only_currency_nag_on_long_prose_is_silent_skip(self):
        nag = Output(type="text", payload=_CURRENCY_NAG, meta={})
        kept, silent = _apply_anti_intrusion_guard(_LONG_BANK_SCENARIO_USER, [nag])
        self.assertEqual(kept, [])
        self.assertTrue(silent)

    def test_long_reply_with_currency_sentence_not_fully_intrusive(self):
        long_with = "Раздел 1. " + ("текст " * 80) + "\n\nУточни, пожалуйста, базовую валюту (ISO код)."
        out = Output(type="text", payload=long_with, meta={})
        self.assertFalse(_is_intrusive_service_reply(out))

    def test_fact_confirmation_stripped_when_substantive_reply_exists(self):
        portfolio_user = "смоделируй диверсификацию портфеля из акций и облигаций"
        main = Output(
            type="text",
            payload=(
                "Алексей, в диверсификации портфеля ключевой принцип — распределить риск. "
                "Консервативный вариант: 30% акции, 70% облигации."
            ),
            meta={},
        )
        confirm = Output(
            type="text",
            payload="Запомнить населённый пункт? Ответь «да» или «нет».",
            meta={"confirmation": True},
        )
        kept, silent = _apply_anti_intrusion_guard(portfolio_user, [main, confirm])
        self.assertEqual(kept, [main])
        self.assertFalse(silent)

    def test_reminder_nag_stripped_with_substantive_reply(self):
        user = "в статье про агентов есть конкретное напоминание о дедлайне"
        main = Output(
            type="text",
            payload=(
                "В тексте речь о дедлайне проекта в контексте AI-агентов, а не о функции "
                "бот-напоминаний. Перескажу суть абзаца без предложения создать reminder."
            ),
            meta={},
        )
        nag = Output(
            type="text",
            payload="Не вижу время для напоминания. Напиши, например: напомни завтра в 10.",
            meta={},
        )
        kept, silent = _apply_anti_intrusion_guard(user, [main, nag])
        self.assertEqual(kept, [main])
        self.assertFalse(silent)

    def test_non_currency_intrusive_all_restored_when_empty_kept(self):
        nag = Output(
            type="text",
            payload="Для расчёта выражения отправьте команду вида: /calc 2+2",
            meta={},
        )
        result, silent = _apply_anti_intrusion_guard(_LONG_BANK_SCENARIO_USER, [nag])
        self.assertEqual(result, [nag])
        self.assertFalse(silent)


if __name__ == "__main__":
    unittest.main()
