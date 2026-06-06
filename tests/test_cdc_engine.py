import json
import os
import unittest
from unittest.mock import patch

from core.cdc.engine import (
    apply_route_tier_cap,
    build_policy_for_user,
    classify_reaction_level,
    maybe_apply_planner_penalty,
    outcome_reward,
    process_turn_end,
)
from core.agent_kv.store import reset_agent_kv_connection_cache
from core.task_depth import apply_tier_ceiling
from core.unified_planner import PlannerDecision


class CdcEngineTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_agent_kv_connection_cache()

    def test_outcome_reward(self):
        self.assertEqual(outcome_reward("ok"), 1.0)
        self.assertEqual(outcome_reward("fallback"), -0.5)

    def test_apply_tier_ceiling(self):
        self.assertEqual(apply_tier_ceiling("deep", "nested"), "nested")
        self.assertEqual(apply_tier_ceiling("shallow", "nested"), "shallow")

    def test_build_policy_tier_and_module(self):
        uid = "u1"
        ag = {
            f"{uid}|math|general": {"fail_streak": 3, "reward_ema": -0.2},
            f"{uid}|math|reasoning": {"fail_streak": 1, "reward_ema": 0.0},
            f"{uid}|echo|general": {"fail_streak": 4, "reward_ema": -0.5},
        }
        with patch.dict(os.environ, {"CDC_FAIL_STREAK_TIER_CAP": "3", "CDC_FAIL_STREAK_MODULE_PENALTY": "4"}, clear=False):
            pol = build_policy_for_user(uid, ag)
        self.assertEqual(pol["route_tier_caps"].get("math|general"), "nested")
        self.assertIn("echo", pol["penalized_modules"])

    def test_process_turn_end_writes_and_policy(self):
        agg_p = os.path.join(os.path.dirname(__file__), "_tmp_cdc_agg.json")
        log_p = os.path.join(os.path.dirname(__file__), "_tmp_cdc_log.jsonl")
        for p in (agg_p, log_p):
            try:
                if os.path.isfile(p):
                    os.remove(p)
            except OSError:
                pass
        uid = "cdc_test_user"
        with patch.dict(
            os.environ,
            {
                "CDC_ENGINE_ENABLED": "true",
                "GEMMA_CDC_AGGREGATES_PATH": agg_p,
                "GEMMA_CDC_TURN_LOG": log_p,
                "CDC_FAIL_STREAK_TIER_CAP": "2",
                "CDC_FAIL_STREAK_MODULE_PENALTY": "9",
            },
            clear=False,
        ):
            process_turn_end(
                user_id=uid,
                user_text="hello",
                intent="general",
                module="math",
                outcome="fallback",
                task_tier="deep",
            )
            process_turn_end(
                user_id=uid,
                user_text="hello",
                intent="general",
                module="math",
                outcome="failure",
                task_tier="deep",
            )
        self.assertTrue(os.path.isfile(log_p))
        with open(log_p, "r", encoding="utf-8") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 2)
        row = json.loads(lines[0])
        self.assertIn("reward", row)
        self.assertEqual(row["outcome"], "fallback")
        with open(agg_p, "r", encoding="utf-8") as f:
            blob = json.load(f)
        key = f"{uid}|math|general"
        self.assertEqual(blob[key]["fail_streak"], 2)

    def test_apply_route_tier_cap_from_persisted(self):
        with patch.dict(os.environ, {"CDC_ENGINE_ENABLED": "true"}, clear=False):
            persisted = {
                "cdc_policy": {"route_tier_caps": {"chat_orchestrator|general": "nested"}, "next_turn_tier_floor": "nested"}
            }
            t = apply_route_tier_cap(
                "deep",
                planned_module="chat-orchestrator",
                planned_intent="general",
                persisted=persisted,
            )
        self.assertEqual(t, "nested")
        self.assertNotIn("next_turn_tier_floor", persisted.get("cdc_policy", {}))

    def test_maybe_apply_planner_penalty(self):
        allowed = {"chat-orchestrator", "math", "echo"}
        d = PlannerDecision(module_name="math", intent="general", reason="test", fallback=False)
        persisted = {"cdc_policy": {"penalized_modules": ["math"]}}
        with patch.dict(os.environ, {"CDC_ENGINE_ENABLED": "true"}, clear=False):
            d2 = maybe_apply_planner_penalty(d, persisted, allowed)
        self.assertEqual(d2.module_name, "chat-orchestrator")
        self.assertIn("cdc_module_penalty", d2.reason)

    def test_reaction_levels(self):
        self.assertEqual(
            classify_reaction_level(bucket={"fail_streak": 1, "n_bad": 1, "v_p": 0.1}, outcome="error", error_type="tool"),
            "local",
        )
        self.assertEqual(
            classify_reaction_level(bucket={"fail_streak": 4, "n_bad": 4, "v_p": 0.4}, outcome="fallback", error_type="router"),
            "route",
        )
        self.assertEqual(
            classify_reaction_level(bucket={"fail_streak": 2, "n_bad": 2, "v_p": 0.9}, outcome="error", error_type="policy"),
            "policy",
        )

    def test_process_turn_end_writes_policy_versions(self):
        agg_p = os.path.join(os.path.dirname(__file__), "_tmp_cdc_agg2.json")
        log_p = os.path.join(os.path.dirname(__file__), "_tmp_cdc_log2.jsonl")
        kv_p = os.path.join(os.path.dirname(__file__), "_tmp_cdc_kv2.sqlite")
        for p in (agg_p, log_p, kv_p):
            try:
                if os.path.isfile(p):
                    os.remove(p)
            except OSError:
                pass
        with patch.dict(
            os.environ,
            {
                "CDC_ENGINE_ENABLED": "true",
                "AGENT_KV_ENABLED": "true",
                "AGENT_KV_SQLITE_PATH": kv_p,
                "GEMMA_CDC_AGGREGATES_PATH": agg_p,
                "GEMMA_CDC_TURN_LOG": log_p,
            },
            clear=False,
        ):
            process_turn_end(
                user_id="u55",
                user_text="x",
                intent="general",
                module="chat",
                outcome="fallback",
                task_tier="deep",
                detail="planner route fallback",
            )
            process_turn_end(
                user_id="u55",
                user_text="x",
                intent="general",
                module="chat",
                outcome="ok",
                task_tier="nested",
            )
        with open(log_p, "r", encoding="utf-8") as f:
            rows = [json.loads(x) for x in f if x.strip()]
        self.assertEqual(len(rows), 2)
        self.assertIn("policy_version_before", rows[1])
        self.assertIn("policy_version_after", rows[1])
        self.assertEqual(rows[0].get("error_type"), "router")


if __name__ == "__main__":
    unittest.main()
