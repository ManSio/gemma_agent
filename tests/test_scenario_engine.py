"""Проактивный движок сценариев."""

import unittest

from core.models import Output
from core.scenario_engine import (
    TurnContext,
    TurnForecast,
    apply_forecast_to_facts_flow,
    apply_post_execute,
    forecast_from_dict,
    forecast_pre_turn,
)


class ScenarioEngineTests(unittest.TestCase):
    def test_finance_suppresses_fact_confirm(self):
        ctx = TurnContext(
            user_text="смоделируй диверсификацию портфеля из акций и облигаций",
            intent="general",
        )
        fc = forecast_pre_turn(ctx)
        self.assertTrue(fc.suppress_fact_confirmation)
        ff = apply_forecast_to_facts_flow(
            {"confirmation_prompt": "Запомнить город?"},
            fc,
        )
        self.assertNotIn("confirmation_prompt", ff)

    def test_reminder_in_prose_brain_hint(self):
        ctx = TurnContext(
            user_text=(
                "В статье про AI-агентов автор пишет о важности конкретного напоминания "
                "о дедлайне для команды разработки."
            ),
        )
        fc = forecast_pre_turn(ctx)
        ids = [h.id for h in fc.hits]
        self.assertIn("reminder_word_in_prose", ids)
        self.assertTrue(any("reminder" in ln.lower() for ln in fc.brain_hint_lines))

    def test_post_execute_dedupes(self):
        user = "какой цвет стула в комнате"
        main = Output(
            type="text",
            payload=(
                "Алексей, для стула в гостиной часто выбирают нейтральные оттенки: "
                "бежевый, серый или тёплый дуб — они не спорят с интерьером."
            ),
            meta={},
        )
        off = Output(
            type="text",
            payload=(
                "Алексей, в ванной комнате важно продумать сантехнику и вентиляцию. "
                "Унитаз лучше ставить с учётом размера помещения."
            ),
            meta={},
        )
        fc = TurnForecast(expect_multi_answer_risk=True)
        out, hits, silent = apply_post_execute([main, off], user, fc)
        self.assertFalse(silent)
        self.assertEqual(len(out), 1)
        self.assertTrue(
            any(h.action == "dedupe_outputs" for h in hits)
            or any(h.id == "duplicate_substantive_outputs" for h in hits)
        )

    def test_forecast_roundtrip_dict(self):
        ctx = TurnContext(user_text="не так, ты неправильно понял", intent="general")
        fc = forecast_pre_turn(ctx)
        restored = forecast_from_dict(fc.to_dict())
        self.assertTrue(restored.force_anti_intrusion or restored.brain_hint_lines)

    def test_short_ok_reply_not_replaced(self):
        user = "скажи только: ок"
        out = [Output(type="text", payload="Ок", meta={})]
        result, hits, silent = apply_post_execute(out, user)
        self.assertFalse(silent)
        self.assertEqual(len(result), 1)
        self.assertEqual(str(result[0].payload).strip(), "Ок")
        self.assertFalse(any(h.id == "empty_output" for h in hits))

    def test_referential_math_numeric_not_recovered(self):
        user = "к тому числу прибавь 7, снова только число"
        out = [
            Output(
                type="text",
                payload="150",
                meta={"module": "__fallback__", "reason": "referential_math"},
            )
        ]
        result, hits, silent = apply_post_execute(out, user)
        self.assertFalse(silent)
        self.assertEqual(str(result[0].payload).strip(), "150")
        self.assertFalse(any(h.id == "empty_output" for h in hits))
        self.assertNotIn("пустой ответ", str(result[0].payload).lower())

    def test_why_earth_ok_recovered(self):
        from core.scenario_engine import apply_pre_send

        user = "Почему земля круглая"
        out = [Output(type="text", payload="ок", meta={})]
        result, hits, silent = apply_post_execute(out, user)
        self.assertFalse(silent)
        body = str(result[0].payload).strip().lower()
        self.assertNotIn(body, ("ок", "ok"))
        txt, pre_hits = apply_pre_send("ок", user_text=user)
        self.assertNotEqual(txt.strip().lower(), "ок")
        self.assertTrue(any(h.id == "pre_send_trivial_ack" for h in pre_hits))

    def test_substantive_question_brain_hint(self):
        ctx = TurnContext(user_text="Почему земля круглая", intent="explain")
        fc = forecast_pre_turn(ctx)
        self.assertTrue(
            any(h.id == "substantive_question" for h in fc.hits)
            or any("ок" in ln for ln in fc.brain_hint_lines)
        )

    def test_capital_minsk_short_reply_not_empty_fallback(self):
        user = "столица минска"
        out = [Output(type="text", payload="Минск.", meta={})]
        result, hits, silent = apply_post_execute(out, user)
        self.assertFalse(silent)
        self.assertFalse(any(h.id == "empty_output" for h in hits))
        body = str(result[0].payload).strip()
        self.assertNotIn("Не удалось сформировать", body)
        self.assertIn("Минск", body)
        self.assertGreater(len(body), 12)

    def test_davay_continuation_short_reply_not_empty_fallback(self):
        user = "давай"
        last = "могу подсказать, с чего начать."
        out = [
            Output(
                type="text",
                payload="Давай. С чего начнём: ComfyUI или Forge?",
                meta={},
            )
        ]
        result, hits, silent = apply_post_execute(
            out,
            user,
            last_assistant=last,
        )
        self.assertFalse(silent)
        self.assertFalse(any(h.id == "empty_output" for h in hits))
        body = str(result[0].payload).strip()
        self.assertNotIn("Не удалось сформировать", body)
        self.assertIn("Давай", body)


if __name__ == "__main__":
    unittest.main()
