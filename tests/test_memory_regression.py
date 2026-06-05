"""Memory Ops v1: регрессионный корпус без LongMemEval (W1)."""
from __future__ import annotations

import unittest

from core.brain.profile_route_guard import preflight_profile
from core.geo_nearby_reply import try_geo_nearby_reply_sync
from core.heuristic_context_gate import should_run_shortcut
from tests.test_heuristic_false_positives import DENTAL_RYADOM


class MemoryRegressionTests(unittest.TestCase):
    def test_short_tier_identity_anchor_set(self) -> None:
        from core.brain.prompt_modules import _user_facts_has_identity_anchor

        self.assertTrue(_user_facts_has_identity_anchor({"pet_cat": "Мурза"}))

    def test_habr_url_not_math(self) -> None:
        url = "https://habr.com/ru/articles/999999/"
        pre = preflight_profile(f"Перескажи кратко {url}")
        self.assertIn(pre, ("summarization", "quick_explain"))

    def test_dental_not_geo_shortcut(self) -> None:
        self.assertIsNone(try_geo_nearby_reply_sync(DENTAL_RYADOM))
        gr = should_run_shortcut("geo_nearby", DENTAL_RYADOM)
        self.assertFalse(gr.allowed)

    def test_explicit_nearby_gate_allowed(self) -> None:
        gr = should_run_shortcut("geo_nearby", "что рядом")
        self.assertTrue(gr.allowed)

    def test_financial_prose_not_math_profile(self) -> None:
        from core.brain.profile_registry import profile_from_text_heuristics

        story = (
            "день 1: баланс 1000, налог 13%. посчитай итоговую оценку риска "
            "по сценарию usd eur byn — таблица итераций формул " + "подробно " * 12
        )
        self.assertIsNone(profile_from_text_heuristics(story))

    def test_short_math_still_profile(self) -> None:
        from core.brain.profile_registry import profile_from_text_heuristics

        self.assertEqual(profile_from_text_heuristics("посчитай 2+2"), "math_solve")

    def test_topic_gate_hint_nonempty(self) -> None:
        from core.heuristic_context_gate import build_topic_gate_hint

        h = build_topic_gate_hint({"current": "кошка Мурка"})
        self.assertIn("Мурка", h)


if __name__ == "__main__":
    unittest.main()
