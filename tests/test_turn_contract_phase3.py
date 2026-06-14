"""Tests for Phase 3: lane ops, additive prompt, regression suite."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from core.brain.prompt_modules import build_dynamic_tail
from core.turn_contract import LANE_DEEP, LANE_DIALOGUE, LANE_FACT
from core.turn_lane_ops import (
    format_lane_summary_short,
    lane_from_turn_row,
    lane_label_ru,
    summarize_lane_distribution,
)
from core.turn_prompt_additive import (
    additive_prompt_enabled,
    prepare_additive_context,
    profile_hop_detected,
    record_active_modules,
    resolve_force_modules,
)
from core.turn_regression import default_regression_fixture_path, load_regression_cases, replay_case, run_regression_suite
from core.turn_meaning import ACTION_STAY


class TestTurnLaneOps(unittest.TestCase):
    def test_lane_from_turn_row_prefers_contract_lane(self) -> None:
        row = {"lane": "FACT", "profile": "standard"}
        self.assertEqual(lane_from_turn_row(row), LANE_FACT)

    def test_lane_from_profile_fallback(self) -> None:
        row = {"profile": "research", "short_circuit": ""}
        self.assertEqual(lane_from_turn_row(row), LANE_DEEP)

    def test_summarize_distribution(self) -> None:
        rows = [
            {"lane": "DIALOGUE"},
            {"lane": "FACT"},
            {"lane": "DIALOGUE"},
        ]
        s = summarize_lane_distribution(rows)
        self.assertEqual(s["counts"][LANE_DIALOGUE], 2)
        self.assertEqual(s["counts"][LANE_FACT], 1)
        self.assertEqual(s["lane_hops"], 2)

    def test_label_ru(self) -> None:
        self.assertEqual(lane_label_ru(LANE_FACT), "факт")

    def test_format_short(self) -> None:
        s = format_lane_summary_short({"counts": {LANE_DIALOGUE: 3, LANE_FACT: 1, LANE_DEEP: 0}})
        self.assertIn("D=3", s)
        self.assertIn("F=1", s)


class TestTurnPromptAdditive(unittest.TestCase):
    def test_profile_hop_detected(self) -> None:
        ctx = {"dialogue_state": {"last_brain_profile": "standard"}, "brain_profile": "research"}
        self.assertTrue(profile_hop_detected(ctx, "research"))

    def test_stay_keeps_sticky_modules(self) -> None:
        ctx = {
            "dialogue_state": {
                "sticky_prompt_modules": ["topic_anchor"],
                "last_brain_profile": "standard",
            },
            "turn_meaning": {"thread_action": ACTION_STAY},
            "brain_profile": "standard",
        }
        force = resolve_force_modules(ctx, profile="standard")
        self.assertIn("topic_anchor", force)

    def test_hop_adds_sticky(self) -> None:
        ctx = {
            "dialogue_state": {
                "sticky_prompt_modules": ["active_thread"],
                "last_brain_profile": "standard",
            },
            "brain_profile": "research",
        }
        force = resolve_force_modules(ctx, profile="research")
        self.assertIn("active_thread", force)

    def test_prepare_context_sets_force(self) -> None:
        ctx = {
            "dialogue_state": {"sticky_prompt_modules": ["topic_anchor"], "last_brain_profile": "a"},
            "brain_profile": "b",
            "turn_meaning": {"thread_action": ACTION_STAY},
        }
        prepare_additive_context(ctx, profile="b")
        self.assertIn("topic_anchor", ctx.get("_force_prompt_modules") or [])

    def test_record_merges_modules(self) -> None:
        ctx: dict = {"dialogue_state": {"sticky_prompt_modules": ["topic_anchor"]}}
        record_active_modules(ctx, ["active_thread"])
        sticky = ctx["dialogue_state"]["sticky_prompt_modules"]
        self.assertEqual(sticky, ["topic_anchor", "active_thread"])

    def test_disabled_flag(self) -> None:
        with mock.patch.dict("os.environ", {"TURN_PROMPT_ADDITIVE_ENABLED": "false"}):
            self.assertFalse(additive_prompt_enabled())
            ctx = {"dialogue_state": {"sticky_prompt_modules": ["topic_anchor"]}, "brain_profile": "x"}
            self.assertEqual(resolve_force_modules(ctx, profile="y"), set())


class TestPromptModulesAdditive(unittest.TestCase):
    def test_force_module_included(self) -> None:
        from core.brain import prompt_modules as pm

        def _pred(parts, cfg, intent, ctx):
            return False

        def _content(parts, cfg):
            return "FORCED_BLOCK"

        mod = ("_test_forced_mod_phase3", _pred, _content)
        pm._MODULES.append(mod)
        try:
            parts: dict = {}
            ctx = {"_force_prompt_modules": ["_test_forced_mod_phase3"]}
            tail = build_dynamic_tail(parts, "standard", "general", ctx)
            self.assertIn("FORCED_BLOCK", tail)
            self.assertIn("_test_forced_mod_phase3", parts.get("_prompt_modules_active") or [])
        finally:
            pm._MODULES.remove(mod)


class TestTurnRegressionSuite(unittest.TestCase):
    def test_fixture_has_20_cases(self) -> None:
        cases = load_regression_cases(default_regression_fixture_path())
        self.assertEqual(len(cases), 20)

    def test_regression_suite_all_pass(self) -> None:
        rep = run_regression_suite()
        if rep.get("failed"):
            fails = [r for r in rep.get("results") or [] if not r.get("ok")]
            self.fail(f"regression failures: {json.dumps(fails, ensure_ascii=False)}")

    def test_replay_case_shape(self) -> None:
        cases = load_regression_cases()
        row = replay_case(cases[0])
        self.assertIn("ok", row)
        self.assertIn("got", row)


if __name__ == "__main__":
    unittest.main()
