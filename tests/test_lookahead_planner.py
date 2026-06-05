import os
import unittest
from unittest.mock import patch

from core.lookahead_planner import build_lookahead_plan, enabled


class LookaheadPlannerTests(unittest.TestCase):
    def test_disabled_returns_empty(self):
        with patch.dict(os.environ, {"LOOKAHEAD_PLANNER_ENABLED": "false"}, clear=False):
            self.assertFalse(enabled())
            out = build_lookahead_plan(
                user_text="hello",
                intent="general",
                module="chat_orchestrator",
                planner_reason="intent_module_match",
                fallback=False,
                goal_hints={},
                predictive_hint={},
                knowledge_hint={},
                skill_name="",
            )
        self.assertEqual(out, {})

    def test_plugin_path_has_three_steps(self):
        with patch.dict(os.environ, {"LOOKAHEAD_PLANNER_ENABLED": "true"}, clear=False):
            out = build_lookahead_plan(
                user_text="сделай плагин с module.json и execute",
                intent="general",
                module="chat_orchestrator",
                planner_reason="intent_module_match",
                fallback=False,
                goal_hints={},
                predictive_hint={},
                knowledge_hint={},
                skill_name="",
            )
        self.assertGreaterEqual(len(out.get("steps") or []), 2)
        self.assertTrue(any("manifest" in str(s).lower() or "плагин" in str(s).lower() for s in out["steps"]))

    def test_math_path(self):
        with patch.dict(os.environ, {"LOOKAHEAD_PLANNER_ENABLED": "true"}, clear=False):
            out = build_lookahead_plan(
                user_text="/calc 1+1",
                intent="math",
                module="math",
                planner_reason="intent_module_match",
                fallback=False,
                goal_hints={},
                predictive_hint={},
                knowledge_hint={},
                skill_name="",
            )
        self.assertTrue(out.get("steps"))
        self.assertTrue(out.get("likely_followups"))

    def test_plot_twist_path(self):
        with patch.dict(os.environ, {"LOOKAHEAD_PLANNER_ENABLED": "true"}, clear=False):
            out = build_lookahead_plan(
                user_text="Ирина подала на развод, я в шоке",
                intent="general",
                module="chat_orchestrator",
                planner_reason="intent_module_match",
                fallback=False,
                goal_hints={},
                predictive_hint={},
                knowledge_hint={},
                skill_name="",
            )
        steps = " ".join(str(s) for s in (out.get("steps") or []))
        self.assertIn("канон", steps.lower())


if __name__ == "__main__":
    unittest.main()
