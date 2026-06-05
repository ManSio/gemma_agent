"""Динамический стиль ответа (autonomy style) без CDC."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from core import self_model as sm


class SelfModelAutonomyTests(unittest.TestCase):
    def test_cautious_low_score(self):
        base = {
            "confidence_summary": {"score": 0.4},
            "recent_outcomes": [{"outcome": "ok"}, {"outcome": "ok"}],
        }
        self.assertEqual(sm.compute_response_style_mode(base), "cautious")

    def test_cautious_many_clarify(self):
        base = {
            "confidence_summary": {"score": 0.9},
            "recent_outcomes": [
                {"outcome": "clarify"},
                {"outcome": "ok"},
                {"outcome": "clarify"},
            ],
        }
        self.assertEqual(sm.compute_response_style_mode(base), "cautious")

    def test_assertive_high_score(self):
        base = {
            "confidence_summary": {"score": 0.85},
            "recent_outcomes": [{"outcome": "ok"}, {"outcome": "ok"}],
        }
        self.assertEqual(sm.compute_response_style_mode(base), "assertive")

    def test_addon_includes_style_when_enabled(self):
        with patch.dict(
            os.environ,
            {
                "SELF_MODEL_ENABLED": "true",
                "SELF_MODEL_PROMPT_ADDON_ENABLED": "true",
                "SELF_MODEL_AUTONOMY_STYLE_ENABLED": "true",
            },
            clear=False,
        ):
            txt = sm.self_model_trust_addon_for_prompt(
                {"confidence_summary": {"score": 0.85}, "recent_outcomes": [{"outcome": "ok"}]}
            )
        self.assertIn("Режим ответа", txt)
        self.assertIn("уверенно", txt.lower())

    def test_extended_block_shape(self):
        base = {
            "confidence_summary": {"score": 0.7},
            "limits": {"no_force_external_state": True, "context_is_probabilistic": True},
            "recent_outcomes": [
                {"outcome": "ok", "intent": "general", "module": "chat_orchestrator"},
                {"outcome": "clarify", "intent": "general", "module": "chat_orchestrator"},
            ],
            "last_route": {"module": "chat_orchestrator", "intent": "general"},
        }
        b = sm.compute_extended_dynamic_block(base)
        self.assertIn("trust_state", b)
        self.assertIn("memory", b["trust_state"])
        self.assertGreaterEqual(float(b["clarify_rate"]), 0.0)

    def test_extended_disabled_without_style(self):
        with patch.dict(
            os.environ,
            {
                "SELF_MODEL_AUTONOMY_STYLE_ENABLED": "false",
                "SELF_MODEL_AUTONOMY_EXTENDED_ENABLED": "true",
            },
            clear=False,
        ):
            self.assertFalse(sm.autonomy_extended_enabled())

    def test_goal_addon(self):
        with patch.dict(os.environ, {"SELF_MODEL_AUTONOMY_GOAL_CONTEXT_ENABLED": "true"}, clear=False):
            s = sm.autonomy_goal_addon_for_prompt(
                {"autonomy_goal": {"summary": "Собрать отчёт", "max_tool_calls": 4, "step": "2/3"}}
            )
        self.assertIn("Собрать отчёт", s)
        self.assertIn("2/3", s)
        self.assertIn("4", s)

    def test_hydrate_from_lookahead_skips_short_text(self):
        ctx: dict = {}
        with patch.dict(os.environ, {"SELF_MODEL_AUTONOMY_GOAL_CONTEXT_ENABLED": "true"}, clear=False):
            sm.hydrate_autonomy_goal_from_runtime(
                ctx,
                user_text="ок",
                goal_hints={},
                lookahead_plan=None,
                planned_intent="general",
                task_tier="shallow",
            )
        self.assertNotIn("autonomy_goal", ctx)

    def test_hydrate_from_goals_and_lookahead(self):
        ctx = {}
        lap = {"steps": [{"do": "Проверить формат", "why": "x"}]}
        gh = {"mission": "Помочь с кодом", "active_goals": [{"text": "быстрее правки", "status": "active"}]}
        with patch.dict(os.environ, {"SELF_MODEL_AUTONOMY_GOAL_CONTEXT_ENABLED": "true"}, clear=False):
            sm.hydrate_autonomy_goal_from_runtime(
                ctx,
                user_text="x",
                goal_hints=gh,
                lookahead_plan=lap,
                planned_intent="reasoning",
                task_tier="nested",
            )
        self.assertIn("autonomy_goal", ctx)
        self.assertIn("Помочь", ctx["autonomy_goal"]["summary"])
        self.assertIn("Проверить формат", ctx["autonomy_goal"]["summary"])
        self.assertEqual(ctx["autonomy_goal"]["step"], "tier=nested")


if __name__ == "__main__":
    unittest.main()
