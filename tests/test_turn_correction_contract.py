"""Tests for correction turn contract."""
from __future__ import annotations

import unittest

from core.turn_correction_contract import (
    apply_correction_override,
    is_correction_turn,
    must_blocks_for_context,
)
from core.turn_meaning import ACTION_CORRECT


class TestTurnCorrectionContract(unittest.TestCase):
    def test_correction_detected(self) -> None:
        self.assertTrue(
            is_correction_turn({"turn_meaning": {"thread_action": ACTION_CORRECT}})
        )

    def test_must_blocks_on_correction(self) -> None:
        blocks = must_blocks_for_context(
            {"turn_meaning": {"thread_action": ACTION_CORRECT, "speech_act": "correction"}}
        )
        self.assertIn("user_correction", blocks)
        self.assertIn("topic_anchor", blocks)

    def test_force_full_prompt(self) -> None:
        ctx = apply_correction_override(
            {"turn_meaning": {"thread_action": ACTION_CORRECT}, "turn_contract": {}}
        )
        self.assertTrue(ctx.get("brain_force_full_prompt"))
        self.assertTrue(ctx.get("correction_turn"))


if __name__ == "__main__":
    unittest.main()
