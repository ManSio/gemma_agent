import json
import os
import unittest
from unittest.mock import patch

from core.task_depth import (
    apply_tier_ceiling,
    apply_task_tier_hysteresis,
    infer_task_tier,
    infer_task_tier_with_history,
    max_task_tier,
    refine_task_tier_from_outline,
    tier_prefers_thorough,
)
from core.strategy_path_memory import append_strategy_success, build_strategy_path_hint


class TaskDepthTests(unittest.TestCase):
    def test_shallow(self):
        self.assertEqual(infer_task_tier("Привет"), "shallow")

    def test_nested(self):
        self.assertEqual(infer_task_tier("Как дела? И ещё: что по погоде?"), "nested")

    def test_deep_numbered(self):
        self.assertEqual(infer_task_tier("1) foo\n2) bar\n3) baz"), "deep")

    def test_plot_twist_nested(self):
        self.assertEqual(infer_task_tier("Она подала на развод вчера"), "nested")

    def test_tier_prefers_thorough(self):
        self.assertTrue(tier_prefers_thorough("deep"))
        self.assertFalse(tier_prefers_thorough("shallow"))

    def test_scenario_marker_nested(self):
        self.assertIn(infer_task_tier("Сценарный анализ рисков при смене работы"), ("nested", "deep"))

    def test_uncertainty_markers_boost_tier(self):
        """Короткий текст с неопределённостью — не shallow (сценарный контур)."""
        self.assertIn(
            infer_task_tier(
                "Банк в техработах, срок неизвестен, карта может не пройти в отеле."
            ),
            ("nested", "deep"),
        )
        self.assertIn(
            infer_task_tier("Что если ограничения не снимут до отъезда через 10 дней?"),
            ("nested", "deep"),
        )

    def test_infer_task_tier_with_history(self):
        d = [
            {"role": "user", "text": "Первый вопрос?"},
            {"role": "assistant", "text": "…"},
            {"role": "user", "text": "Второй вопрос тогда?"},
        ]
        self.assertEqual(infer_task_tier("продолжай"), "shallow")
        self.assertEqual(infer_task_tier_with_history("продолжай", d), "nested")

    def test_weak_continuation_caps_blob_deep(self):
        d = [{"role": "user", "text": "А? Б? В? И ещё про риски?"}]
        self.assertEqual(infer_task_tier_with_history("продолжай", d), "nested")

    def test_hysteresis_at_most_one_step_down(self):
        self.assertEqual(apply_task_tier_hysteresis("shallow", "deep"), "nested")
        self.assertEqual(apply_task_tier_hysteresis("nested", "deep"), "nested")
        self.assertEqual(apply_task_tier_hysteresis("shallow", "nested"), "shallow")
        self.assertEqual(apply_task_tier_hysteresis("deep", "deep"), "deep")
        self.assertEqual(apply_task_tier_hysteresis("deep", "shallow"), "deep")

    def test_math_intent_caps_numbered_deep(self):
        txt = "1) foo\n2) bar"
        self.assertEqual(infer_task_tier(txt), "deep")
        self.assertEqual(infer_task_tier_with_history(txt, None, planned_intent="math"), "nested")

    def test_terse_mode_caps_short_deep(self):
        txt = "1) a\n2) b\n3) c"
        self.assertEqual(infer_task_tier(txt), "deep")
        self.assertEqual(infer_task_tier_with_history(txt, None, terse_mode=True), "nested")

    def test_max_task_tier(self):
        self.assertEqual(max_task_tier("shallow", "nested", "deep"), "deep")

    def test_apply_tier_ceiling(self):
        self.assertEqual(apply_tier_ceiling("deep", "nested"), "nested")
        self.assertEqual(apply_tier_ceiling("nested", "nested"), "nested")
        self.assertEqual(apply_tier_ceiling("nested", None), "nested")

    def test_refine_task_tier_from_outline(self):
        self.assertEqual(refine_task_tier_from_outline("shallow", {"depth": "multi"}), "nested")
        self.assertEqual(
            refine_task_tier_from_outline(
                "shallow",
                {"scenarios": [{"branch": "если А", "implication": "то Б"}]},
            ),
            "nested",
        )


class StrategyPathMemoryTests(unittest.TestCase):
    def test_append_and_hint(self):
        p = os.path.join(os.path.dirname(__file__), "_tmp_strategy_paths.jsonl")
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass
        la = {"steps": [{"do": "Шаг А", "why": "x"}, {"do": "Шаг Б", "why": "y"}]}
        txt = "уникальный многоуровневый запрос для стратегии"
        with patch.dict(
            os.environ,
            {
                "GEMMA_STRATEGY_PATH": p,
                "STRATEGY_PATH_MEMORY_ENABLED": "true",
                "STRATEGY_PATH_HINT_FOR_SHALLOW": "true",
            },
            clear=False,
        ):
            append_strategy_success(
                user_text=txt,
                intent="general",
                module="chat",
                task_tier="nested",
                lookahead_plan=la,
                assistant_excerpt="ок ответ",
                path=p,
            )
            h = build_strategy_path_hint(user_text=txt, intent="general", task_tier="nested", path=p)
            h2 = build_strategy_path_hint(
                user_text="она меня бросила, всё кончено",
                intent="general",
                task_tier="nested",
                path=p,
            )
        self.assertIn("Шаг А", h)
        self.assertIn("Шаг Б", h)
        self.assertEqual(h2, "")


if __name__ == "__main__":
    unittest.main()
