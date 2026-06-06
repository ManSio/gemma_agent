import os
import unittest
from unittest.mock import patch

from core.affect_state import (
    default_affect_state,
    hydrate_affect_from_kv,
    modulate_task_tier_with_affect,
    update_affect_after_turn,
)
from core.agent_kv.store import get_json, reset_agent_kv_connection_cache, set_json


class AffectStateTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_agent_kv_connection_cache()

    def test_modulation_caps_on_high_caution(self):
        t = modulate_task_tier_with_affect("deep", {"caution": 0.95, "fatigue": 0.2, "confidence": 0.3, "focus": 0.4})
        self.assertEqual(t, "shallow")

    def test_modulation_boosts_shallow_when_confident(self):
        t = modulate_task_tier_with_affect("shallow", {"caution": 0.3, "fatigue": 0.2, "confidence": 0.9, "focus": 0.9})
        self.assertEqual(t, "nested")

    def test_update_affect_after_turn_persists(self):
        p = os.path.join(os.path.dirname(__file__), "_kv_affect.sqlite")
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass
        with patch.dict(
            os.environ,
            {"AGENT_KV_ENABLED": "true", "AGENT_KV_SQLITE_PATH": p, "AFFECT_STATE_ENABLED": "true"},
            clear=False,
        ):
            reset_agent_kv_connection_cache()
            s1 = update_affect_after_turn(user_id="u1", outcome="fallback", task_tier="deep", error_type="router")
            self.assertIsInstance(s1, dict)
            s2 = get_json("affect", "u1")
            self.assertIsInstance(s2, dict)
            self.assertGreater(float(s2.get("caution") or 0.0), 0.5)

    def test_hydrate_affect_from_kv(self):
        p = os.path.join(os.path.dirname(__file__), "_kv_affect_hydr.sqlite")
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass
        with patch.dict(
            os.environ,
            {"AGENT_KV_ENABLED": "true", "AGENT_KV_SQLITE_PATH": p, "AFFECT_STATE_ENABLED": "true"},
            clear=False,
        ):
            reset_agent_kv_connection_cache()
            set_json("affect", "u2", default_affect_state())
            rec = hydrate_affect_from_kv("u2", {"x": 1})
            self.assertIn("affect_state", rec)


if __name__ == "__main__":
    unittest.main()

