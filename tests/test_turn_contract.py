"""Tests for TurnContract and dialogue STM helpers."""
from __future__ import annotations

import tempfile
import unittest

from core.behavior_store import BehaviorStore
from core.turn_contract import (
    LANE_FACT,
    build_turn_contract,
    recent_dialogue_fingerprint,
    turn_contract_enabled,
)


class TestTurnContract(unittest.TestCase):
    def test_fingerprint_stable(self) -> None:
        rd = [
            {"role": "user", "text": "привет"},
            {"role": "assistant", "text": "здравствуйте"},
        ]
        a = recent_dialogue_fingerprint(rd)
        b = recent_dialogue_fingerprint(rd)
        self.assertEqual(a, b)
        self.assertEqual(16, len(a))

    def test_build_contract_lane_fact(self) -> None:
        tc = build_turn_contract(
            trace_id="abc",
            generation=3,
            turn_meaning={"referent": "thread", "thread_action": "stay"},
            user_text="какая погода",
            short_circuit="weather_direct",
            profile="standard",
        )
        self.assertEqual(tc.generation, 3)
        self.assertEqual(tc.lane, LANE_FACT)
        self.assertEqual(tc.referent, "thread")
        d = tc.to_dict()
        self.assertEqual(d["short_circuit"], "weather_direct")

    def test_turn_contract_enabled_default(self) -> None:
        self.assertTrue(turn_contract_enabled())


class TestBehaviorStoreTurnGeneration(unittest.TestCase):
    def test_bump_and_reconcile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = BehaviorStore(base_dir=tmp)
            uid = "u1"
            store.update_after_turn(uid, None, "hi", "hello")
            g1 = store.bump_turn_generation(uid, None)
            g2 = store.bump_turn_generation(uid, None)
            self.assertEqual(g2, g1 + 1)
            ok = store.reconcile_sent_assistant_text(
                uid, None, "hello patched", generation=g2
            )
            self.assertTrue(ok)
            rec = store.load(uid, None)
            last = rec["recent_messages"][-1]
            self.assertEqual(last["text"], "hello patched")

    def test_refresh_dialogue_stm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = BehaviorStore(base_dir=tmp)
            uid = "u2"
            store.update_after_turn(uid, None, "a", "b")
            stale = {"recent_messages": [{"role": "user", "text": "stale"}]}
            fresh = store.refresh_dialogue_stm_from_disk(uid, None, stale)
            self.assertNotEqual(fresh["recent_messages"][0]["text"], "stale")


if __name__ == "__main__":
    unittest.main()
