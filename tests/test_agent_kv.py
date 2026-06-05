import os
import unittest
from unittest.mock import patch

from core.agent_kv.grim import hydrate_cdc_from_kv, update_grim_after_turn
from core.agent_kv.policy import sweep_agent_kv
from core.agent_kv.router_stats import record_router_turn
from core.agent_kv.store import (
    agent_kv_branch,
    get_history,
    get_json,
    reset_agent_kv_connection_cache,
    rollback_to_version,
    set_json,
)


class AgentKVTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_agent_kv_connection_cache()

    def test_set_get_version_history_rollback(self):
        p = os.path.join(os.path.dirname(__file__), "_kv_test.sqlite")
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass
        with patch.dict(
            os.environ,
            {"AGENT_KV_ENABLED": "true", "AGENT_KV_SQLITE_PATH": p, "AGENT_KV_BRANCH": "main"},
            clear=False,
        ):
            reset_agent_kv_connection_cache()
            v1 = set_json("ns", "k", {"a": 1}, ttl_sec=None, priority=1)
            self.assertEqual(v1, 1)
            v2 = set_json("ns", "k", {"a": 2})
            self.assertEqual(v2, 2)
            g = get_json("ns", "k")
            self.assertEqual(g.get("a"), 2)
            hist = get_history("ns", "k", limit=5)
            self.assertGreaterEqual(len(hist), 2)
            ok = rollback_to_version("ns", "k", 1)
            self.assertTrue(ok)
            g2 = get_json("ns", "k")
            self.assertEqual(g2.get("a"), 1)

    def test_hydrate_cdc_merges_grim(self):
        p = os.path.join(os.path.dirname(__file__), "_kv_hydr.sqlite")
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass
        with patch.dict(
            os.environ,
            {"AGENT_KV_ENABLED": "true", "AGENT_KV_SQLITE_PATH": p},
            clear=False,
        ):
            reset_agent_kv_connection_cache()
            br = agent_kv_branch()
            set_json("cdc_policy", "u1", {"route_tier_caps": {}}, branch=br)
            set_json(
                "grim",
                "u1",
                {"active": True, "tier_ceiling": "nested", "force_dialog": False},
                branch=br,
            )
            out = hydrate_cdc_from_kv("u1", {"cdc_policy": {}})
            pol = out.get("cdc_policy") or {}
            self.assertTrue(pol.get("grim_active"))

    def test_grim_clears_on_success_streak(self):
        p = os.path.join(os.path.dirname(__file__), "_kv_grim.sqlite")
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass
        with patch.dict(
            os.environ,
            {
                "AGENT_KV_ENABLED": "true",
                "AGENT_KV_SQLITE_PATH": p,
                "GRIM_FORGIVE_SUCCESS_STREAK": "1",
            },
            clear=False,
        ):
            reset_agent_kv_connection_cache()
            set_json("grim", "u2", {"active": True, "level": "firm"}, branch=agent_kv_branch())
            update_grim_after_turn(
                "u2",
                outcome="ok",
                agg_bucket={"fail_streak": 0, "success_streak": 2},
                module="m",
                intent="i",
            )
            g = get_json("grim", "u2", branch=agent_kv_branch())
            self.assertFalse(g.get("active"))

    def test_router_rollup(self):
        p = os.path.join(os.path.dirname(__file__), "_kv_router.sqlite")
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass
        with patch.dict(os.environ, {"AGENT_KV_ENABLED": "true", "AGENT_KV_SQLITE_PATH": p}, clear=False):
            reset_agent_kv_connection_cache()
            record_router_turn(
                user_id="r1", intent="general", module="chat-orchestrator", outcome="ok", task_tier="nested"
            )
            r = get_json("router", "rollup|r1", branch=agent_kv_branch())
            self.assertGreater(int((r.get("routes") or {}).get("general|chat_orchestrator", {}).get("n") or 0), 0)

    def test_sweep_does_not_crash(self):
        p = os.path.join(os.path.dirname(__file__), "_kv_sweep.sqlite")
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass
        with patch.dict(
            os.environ,
            {"AGENT_KV_ENABLED": "true", "AGENT_KV_SQLITE_PATH": p, "AGENT_KV_HISTORY_TABLE_MAX_ROWS": "50"},
            clear=False,
        ):
            reset_agent_kv_connection_cache()
            for i in range(15):
                set_json("x", "y", {"i": i})
            rep = sweep_agent_kv()
            self.assertIn("path", rep)


if __name__ == "__main__":
    unittest.main()
