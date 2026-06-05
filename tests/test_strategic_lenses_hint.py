"""Блок StrategicLenses в external_hint: только глубокие/сценарные запросы."""
from __future__ import annotations

import os
import unittest

from core.brain.text_helpers import build_strategic_lenses_hint, strategic_lenses_hint_wanted
from core.task_depth import infer_task_tier


class StrategicLensesHintTests(unittest.TestCase):
    def test_shallow_short_no_hint(self):
        self.assertFalse(strategic_lenses_hint_wanted("Как дела?", "shallow"))
        self.assertEqual(build_strategic_lenses_hint("Как дела?", "shallow"), "")

    def test_nested_tier_gets_hint(self):
        t = "Коротко?"
        tier = "nested"
        self.assertTrue(strategic_lenses_hint_wanted(t, tier))
        h = build_strategic_lenses_hint(t, tier)
        self.assertIn("StrategicLenses", h)
        self.assertIn("Детектив", h)

    def test_long_scenario_markers_without_nested_tier(self):
        text = (
            "Ты командуешь обороной. Ограничен ресурс боеприпасов. Два пути: "
            "жертвуешь флангом или центром. Что выберешь? Обоснуй этически."
        )
        self.assertEqual(infer_task_tier(text), "shallow")
        self.assertTrue(strategic_lenses_hint_wanted(text, "shallow"))
        self.assertTrue(len(build_strategic_lenses_hint(text, "shallow")) > 80)

    def test_disabled_via_env(self):
        text = "Дилемма: сценарий с компромиссом " * 5
        tier = "nested"
        self.assertTrue(strategic_lenses_hint_wanted(text, tier))
        old = os.environ.get("STRATEGIC_LENSES_HINT_ENABLED")
        try:
            os.environ["STRATEGIC_LENSES_HINT_ENABLED"] = "0"
            self.assertFalse(strategic_lenses_hint_wanted(text, tier))
            self.assertEqual(build_strategic_lenses_hint(text, tier), "")
        finally:
            if old is None:
                os.environ.pop("STRATEGIC_LENSES_HINT_ENABLED", None)
            else:
                os.environ["STRATEGIC_LENSES_HINT_ENABLED"] = old


if __name__ == "__main__":
    unittest.main()
