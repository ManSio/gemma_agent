"""Reasoning loop: когда включается без LLM."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from core.brain.reasoning_loop_controller import wants_reasoning_loop


class ReasoningLoopWantsTests(unittest.TestCase):
    def test_on_by_default(self):
        with patch.dict("os.environ", {}, clear=False):
            self.assertTrue(
                wants_reasoning_loop("длинный текст " * 20, {}, "deep"),
            )

    def test_off_explicit(self):
        pass

    def test_on_tier_nested(self):
        with patch.dict(
            "os.environ",
            {
                "BRAIN_REASONING_LOOP_ENABLED": "true",
                "BRAIN_REASONING_LOOP_MODE": "tier",
                "BRAIN_REASONING_LOOP_MIN_USER_CHARS": "40",
            },
            clear=False,
        ):
            self.assertTrue(
                wants_reasoning_loop("объясни ветвление сценария и риски " * 3, {}, "nested"),
            )

    def test_shallow_skipped_in_tier_mode(self):
        with patch.dict(
            "os.environ",
            {
                "BRAIN_REASONING_LOOP_ENABLED": "true",
                "BRAIN_REASONING_LOOP_MODE": "tier",
                "BRAIN_REASONING_LOOP_MIN_USER_CHARS": "40",
            },
            clear=False,
        ):
            self.assertFalse(
                wants_reasoning_loop("объясни ветвление сценария и риски " * 3, {}, "shallow"),
            )

    def test_force_context(self):
        with patch.dict(
            "os.environ",
            {"BRAIN_REASONING_LOOP_ENABLED": "true", "BRAIN_REASONING_LOOP_MODE": "tier"},
            clear=False,
        ):
            self.assertTrue(
                wants_reasoning_loop(
                    "коротко",
                    {"brain_force_reasoning_loop": True},
                    "shallow",
                ),
            )

    def test_reasoning_intent_via_dialogue_state(self):
        with patch.dict(
            "os.environ",
            {"BRAIN_REASONING_LOOP_ENABLED": "true", "BRAIN_REASONING_LOOP_MODE": "tier"},
            clear=False,
        ):
            self.assertTrue(
                wants_reasoning_loop(
                    "докажи утверждение и укажи где слабое место",
                    {"dialogue_state": {"last_intent": "reasoning"}},
                    "shallow",
                ),
            )


if __name__ == "__main__":
    unittest.main()
