"""Mutation-style sensitivity tests for TurnContract Phase 2."""
from __future__ import annotations

import os
import unittest
from unittest import mock

from core.short_circuit_registry import all_registered_shortcuts
from core.turn_correction_contract import (
    apply_correction_override,
    correction_override_enabled,
    is_correction_turn,
    must_blocks_for_context,
)
from core.turn_hash import plan_turn_hash, turn_hash_drift_enabled
from core.turn_lane_spine import apply_sticky_lane_and_profile, sticky_lane_enabled
from core.turn_meaning import ACTION_CORRECT, ACTION_STAY


class TestPhase2MutationSensitivity(unittest.TestCase):
    def test_plan_hash_single_field_sensitivity(self) -> None:
        base_kwargs = {"profile": "standard", "lane": "DIALOGUE", "referent": "thread"}
        base = plan_turn_hash(**base_kwargs)
        for field, value in (
            ("profile", "quick_explain"),
            ("lane", "FACT"),
            ("referent", "world"),
            ("short_circuit", "weather_direct"),
            ("module", "brain"),
            ("intent", "news"),
        ):
            mutated_kwargs = dict(base_kwargs)
            mutated_kwargs[field] = value
            mutated = plan_turn_hash(**mutated_kwargs)
            self.assertNotEqual(base, mutated, msg=field)

    def test_sticky_lane_disabled_no_mutation(self) -> None:
        ctx = {
            "turn_meaning": {"thread_action": ACTION_STAY, "inherit_thread": True},
            "discourse_audit": {"action": ACTION_STAY, "inherit_profile": "standard"},
        }
        with mock.patch.dict(os.environ, {"TURN_STICKY_LANE_ENABLED": "false"}):
            self.assertFalse(sticky_lane_enabled())
            out = apply_sticky_lane_and_profile(dict(ctx))
        self.assertNotIn("sticky_lane", out)
        self.assertNotIn("meaning_profile_lock", out)

    def test_sticky_lane_enabled_sets_lane(self) -> None:
        ctx = {
            "turn_meaning": {"thread_action": ACTION_STAY, "inherit_thread": True},
            "discourse_audit": {"action": ACTION_STAY, "inherit_profile": "standard"},
        }
        with mock.patch.dict(os.environ, {"TURN_STICKY_LANE_ENABLED": "true"}):
            out = apply_sticky_lane_and_profile(dict(ctx))
        self.assertIn("sticky_lane", out)
        self.assertEqual(out.get("meaning_profile_lock"), "standard")

    def test_correction_disabled_skips_must_blocks(self) -> None:
        ctx = {"turn_meaning": {"thread_action": ACTION_CORRECT}}
        with mock.patch.dict(os.environ, {"TURN_CORRECTION_OVERRIDE_ENABLED": "false"}):
            self.assertTrue(is_correction_turn(ctx))
            self.assertEqual(must_blocks_for_context(ctx), ())
            out = apply_correction_override(dict(ctx))
        self.assertFalse(out.get("brain_force_full_prompt"))

    def test_correction_enabled_forces_full_prompt(self) -> None:
        ctx = {"turn_meaning": {"thread_action": ACTION_CORRECT}}
        with mock.patch.dict(os.environ, {"TURN_CORRECTION_OVERRIDE_ENABLED": "true"}):
            out = apply_correction_override(dict(ctx))
        self.assertTrue(out.get("brain_force_full_prompt"))
        self.assertIn("user_correction", out.get("turn_contract_must_blocks") or [])

    def test_registry_entries_have_lane(self) -> None:
        for sid, ent in all_registered_shortcuts().items():
            self.assertTrue(ent.get("lane"), msg=f"missing lane: {sid}")

    def test_hash_drift_flag_respected(self) -> None:
        with mock.patch.dict(os.environ, {"TURN_HASH_DRIFT_ENABLED": "false"}):
            self.assertFalse(turn_hash_drift_enabled())


if __name__ == "__main__":
    unittest.main()
