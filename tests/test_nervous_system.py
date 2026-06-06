import os
import unittest
from unittest.mock import patch

from core.agent_kv.store import get_json, reset_agent_kv_connection_cache
from core.event_bus import bus
from core.nervous_system import install_nervous_system


class NervousSystemTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_agent_kv_connection_cache()

    def test_reflex_sets_cdc_policy_after_bad_streak(self):
        p = os.path.join(os.path.dirname(__file__), "_kv_reflex.sqlite")
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
                "NERVOUS_REFLEX_BAD_STREAK": "3",
            },
            clear=False,
        ):
            reset_agent_kv_connection_cache()
            install_nervous_system()
            for _ in range(3):
                bus.emit(
                    "turn.outcome",
                    {
                        "user_id": "u_reflex",
                        "intent": "general",
                        "module": "chat-orchestrator",
                        "outcome": "fallback",
                    },
                )
            pol = get_json("cdc_policy", "u_reflex")
        self.assertIsInstance(pol, dict)
        self.assertEqual(pol.get("next_turn_tier_floor"), "nested")
        self.assertEqual((pol.get("route_tier_caps") or {}).get("chat_orchestrator|general"), "nested")


if __name__ == "__main__":
    unittest.main()

