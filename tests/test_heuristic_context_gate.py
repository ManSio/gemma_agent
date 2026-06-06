"""Context gate для shortcut-правил (W1)."""
from __future__ import annotations

import os
import unittest

from core.heuristic_context_gate import (
    TurnDecisionContext,
    build_turn_decision_context,
    gate_enabled,
    shortcut_allowed,
)
from tests.test_heuristic_false_positives import DENTAL_RYADOM


class HeuristicContextGateTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["HEURISTIC_GATE_ENABLED"] = "true"

    def test_gate_enabled_default(self) -> None:
        os.environ.pop("HEURISTIC_GATE_ENABLED", None)
        self.assertTrue(gate_enabled())

    def test_geo_blocked_on_dental_prose(self) -> None:
        ctx = build_turn_decision_context(DENTAL_RYADOM)
        r = shortcut_allowed("geo_nearby", ctx)
        self.assertEqual(r.verdict, "blocked")
        self.assertIn(
            r.reason,
            ("relational_ryadom_without_explicit_geo", "prose_over_chars", "negative_pattern"),
        )

    def test_geo_allowed_explicit(self) -> None:
        ctx = build_turn_decision_context("кафе рядом")
        r = shortcut_allowed("geo_nearby", ctx)
        self.assertEqual(r.verdict, "allowed")

    def test_weather_direct_allowed(self) -> None:
        ctx = build_turn_decision_context("погода в минске")
        r = shortcut_allowed("weather_direct", ctx)
        self.assertEqual(r.verdict, "allowed")

    def test_weather_direct_blocked_on_long_prose(self) -> None:
        text = "погода " + "x " * 120
        ctx = build_turn_decision_context(text)
        r = shortcut_allowed("weather_direct", ctx)
        self.assertEqual(r.verdict, "blocked")
        self.assertEqual(r.reason, "prose_over_chars")

    def test_batch_detector_allowed_multi_question(self) -> None:
        text = "\n".join(f"вопрос {i}?" for i in range(8))
        ctx = build_turn_decision_context(text)
        r = shortcut_allowed("batch_detector", ctx)
        self.assertEqual(r.verdict, "allowed")

    def test_chitchat_fast_allowed_greeting(self) -> None:
        ctx = build_turn_decision_context("привет")
        r = shortcut_allowed("chitchat_fast_eligible", ctx)
        self.assertEqual(r.verdict, "allowed")

    def test_chitchat_fast_allowed_greeting_with_pending_correction(self) -> None:
        ctx = TurnDecisionContext(
            user_text="привет",
            text_len=6,
            pending_correction=True,
        )
        r = shortcut_allowed("chitchat_fast_eligible", ctx)
        self.assertEqual(r.verdict, "allowed")
        self.assertEqual(r.reason, "ok")

    def test_news_direct_allowed(self) -> None:
        ctx = build_turn_decision_context("что нового в новостях")
        r = shortcut_allowed("news_direct", ctx)
        self.assertEqual(r.verdict, "allowed")

    def test_profile_news_headlines_blocked_on_article(self) -> None:
        from tests.test_news_article_detection import HAVAL_PASTE

        ctx = build_turn_decision_context(HAVAL_PASTE)
        r = shortcut_allowed("profile_news_headlines", ctx)
        self.assertEqual(r.verdict, "blocked")

    def test_news_item_pick_allowed_after_digest(self) -> None:
        digest = "Главные новости\n\n1. Первая\n   · A\n2. Вторая\n   · B"
        ctx = build_turn_decision_context(
            "2",
            planner_context={
                "recent_dialogue": [
                    {"role": "user", "text": "новости"},
                    {"role": "assistant", "text": digest},
                ],
            },
        )
        r = shortcut_allowed("news_item_pick", ctx)
        self.assertEqual(r.verdict, "allowed")

    def test_summarization_gate_blocks_prose(self) -> None:
        ctx = build_turn_decision_context("резюме " + ("текст статьи " * 30))
        r = shortcut_allowed("profile_summarization_substring", ctx)
        self.assertFalse(r.allowed)

    def test_quick_explain_gate_allows_short(self) -> None:
        ctx = build_turn_decision_context("объясни что такое KV cache")
        r = shortcut_allowed("profile_quick_explain_substring", ctx)
        self.assertTrue(r.allowed)

    def test_chitchat_fast_blocked_when_assistant_expects_reply(self) -> None:
        ctx = build_turn_decision_context(
            "ок",
            persisted={
                "dialogue_state": {
                    "last_assistant_text": "Какой вариант ответа верный — A или B?",
                }
            },
        )
        r = shortcut_allowed("chitchat_fast_eligible", ctx)
        self.assertEqual(r.verdict, "blocked")
        self.assertEqual(r.reason, "assistant_expects_reply")

    def test_reminder_schedule_blocked_on_article_prose(self) -> None:
        text = (
            "https://habr.com/article/1 " + "x " * 80
            + " напомни мне про эксперимент с ии-агентами и жюри "
            + "y " * 40
        )
        ctx = build_turn_decision_context(text)
        r = shortcut_allowed("reminder_schedule", ctx)
        self.assertEqual(r.verdict, "blocked")

    def test_reminder_cancel_short_allowed(self) -> None:
        ctx = build_turn_decision_context("отмени напоминание 2")
        r = shortcut_allowed("reminder_cancel", ctx)
        self.assertEqual(r.verdict, "allowed")

    def test_fast_path_blocked_on_long_prose_with_napishi(self) -> None:
        text = "напиши " + ("подробный разбор сценария лечения зубов " * 12)
        ctx = TurnDecisionContext(
            user_text=text,
            text_len=len(text),
            prose_score=0.5,
            fast_path_candidate=True,
        )
        r = shortcut_allowed("fast_path_tool", ctx)
        self.assertEqual(r.verdict, "blocked")

    def test_profile_math_gate_blocks_narrative(self) -> None:
        story = (
            "день 1: баланс 1000, налог 13%. посчитай итог по сценарию " + "x " * 40
        )
        ctx = build_turn_decision_context(story)
        r = shortcut_allowed("profile_math_substring", ctx)
        self.assertEqual(r.verdict, "blocked")

    def test_profile_math_gate_allows_compact(self) -> None:
        ctx = build_turn_decision_context("посчитай 2+2")
        r = shortcut_allowed("profile_math_substring", ctx)
        self.assertEqual(r.verdict, "allowed")

    def test_gate_audit_appended_to_context(self) -> None:
        from core.heuristic_context_gate import append_gate_audit, GateResult

        pc: dict = {}
        append_gate_audit(
            pc,
            GateResult(verdict="blocked", rule_id="geo_nearby", reason="prose_over_chars"),
            topic_current="dental",
        )
        self.assertEqual(len(pc["_heuristic_gate_audit"]), 1)
        self.assertEqual(pc["_heuristic_gate_audit"][0]["gate_verdict"], "blocked")

    def test_router_bypass_blocked_pending_correction(self) -> None:
        ctx = TurnDecisionContext(
            user_text="да",
            text_len=2,
            ultra_short_text=True,
            pending_correction=True,
            last_assistant_text="Продолжить?",
        )
        r = shortcut_allowed("router_bypass_short", ctx)
        self.assertEqual(r.verdict, "blocked")
        self.assertEqual(r.reason, "pending_correction")


if __name__ == "__main__":
    unittest.main()
