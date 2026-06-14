"""Tests for Phase 2 turn lane spine."""
from __future__ import annotations

import unittest

from core.turn_lane_spine import apply_sticky_lane_and_profile, is_sticky_stay_turn
from core.turn_meaning import ACTION_STAY


class TestTurnLaneSpine(unittest.TestCase):
    def test_sticky_stay_detected(self) -> None:
        ctx = {
            "discourse_resolution": {"action": ACTION_STAY, "inherit_profile": "standard"},
            "turn_meaning": {"thread_action": ACTION_STAY, "inherit_thread": True},
        }
        self.assertTrue(is_sticky_stay_turn(ctx))

    def test_correct_not_sticky(self) -> None:
        ctx = {"turn_meaning": {"thread_action": "correct"}}
        self.assertFalse(is_sticky_stay_turn(ctx))

    def test_apply_locks_profile(self) -> None:
        ctx = {
            "discourse_resolution": {"action": ACTION_STAY, "inherit_profile": "quick_explain"},
            "turn_meaning": {"thread_action": ACTION_STAY, "inherit_thread": True},
            "turn_contract": {},
        }
        out = apply_sticky_lane_and_profile(ctx)
        self.assertEqual(out.get("meaning_profile_lock"), "quick_explain")
        self.assertEqual(out.get("sticky_lane"), "DIALOGUE")


if __name__ == "__main__":
    unittest.main()
