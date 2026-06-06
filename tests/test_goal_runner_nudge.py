"""Подсказка /goal_run в контексте мозга при многошаговых формулировках."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from core.brain.goal_runner_nudge import format_goal_runner_routing_addon, warrants_multistep_goal_text


class GoalRunnerNudgeTests(unittest.TestCase):
    def test_empty_when_runner_off(self):
        with patch.dict("os.environ", {"GOAL_RUNNER_ENABLED": "false"}, clear=False):
            s = format_goal_runner_routing_addon("1. сделай А\n2. потом Б")
            self.assertEqual(s, "")

    def test_empty_when_nudge_off(self):
        with patch.dict(
            "os.environ",
            {"GOAL_RUNNER_ENABLED": "true", "GOAL_RUNNER_BRAIN_NUDGE": "false"},
            clear=False,
        ):
            s = format_goal_runner_routing_addon("1. сделай А\n2. потом Б")
            self.assertEqual(s, "")

    def test_numbered_list(self):
        with patch.dict(
            "os.environ",
            {"GOAL_RUNNER_ENABLED": "true", "GOAL_RUNNER_AUTO_START": "false"},
            clear=False,
        ):
            s = format_goal_runner_routing_addon("Список:\n1. открыть страницу\n2. вытащить таблицу")
            self.assertIn("goal_runner_nudge", s)
            self.assertIn("/goal_run", s)

    def test_snachala_potom(self):
        with patch.dict(
            "os.environ",
            {"GOAL_RUNNER_ENABLED": "true", "GOAL_RUNNER_AUTO_START": "false"},
            clear=False,
        ):
            s = format_goal_runner_routing_addon(
                "Сначала найди закон на law.example.com, потом кратко перескажи статью 12"
            )
            self.assertIn("goal_runner_nudge", s)

    def test_nudge_empty_when_auto_start_default(self):
        with patch.dict("os.environ", {"GOAL_RUNNER_ENABLED": "true"}, clear=False):
            s = format_goal_runner_routing_addon("Список:\n1. открыть страницу\n2. вытащить таблицу")
            self.assertEqual(s, "")

    def test_warrants_multistep(self):
        self.assertTrue(
            warrants_multistep_goal_text(
                "Сначала найди закон на law.example.com, потом кратко перескажи статью 12"
            )
        )
        self.assertTrue(
            warrants_multistep_goal_text(
                "Сравни три источника про нейросети для новичка и выведи отличия в двух абзацах"
            )
        )
        self.assertFalse(warrants_multistep_goal_text("/start привет"))
        self.assertFalse(warrants_multistep_goal_text("короткий текст без паттерна достаточной длины х"))

    def test_skip_short(self):
        with patch.dict("os.environ", {"GOAL_RUNNER_ENABLED": "true"}, clear=False):
            s = format_goal_runner_routing_addon("1. а")
            self.assertEqual(s, "")

    def test_skip_goal_command(self):
        with patch.dict("os.environ", {"GOAL_RUNNER_ENABLED": "true"}, clear=False):
            s = format_goal_runner_routing_addon("/goal_run проверить всё")
            self.assertEqual(s, "")


if __name__ == "__main__":
    unittest.main()
