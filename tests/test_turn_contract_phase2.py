"""Integration: defer store + Phase 2 contracts."""
from __future__ import annotations

import tempfile
import unittest

from core.behavior_store import BehaviorStore
from core.models import Output, Plan, PlanStep
from core.turn_delivery_store import defer_turn_store_enabled, persist_turn_after_delivery
from core.turn_contract import build_turn_contract, must_blocks_from_context
from core.turn_meaning import ACTION_CORRECT


class TestTurnContractPhase2Integration(unittest.TestCase):
    def test_correction_must_blocks_in_contract(self) -> None:
        tc = build_turn_contract(
            turn_meaning={"thread_action": ACTION_CORRECT, "speech_act": "correction"},
            user_text="не то",
            context={"turn_meaning": {"thread_action": ACTION_CORRECT}},
        )
        self.assertIn("user_correction", tc.must_blocks)

    def test_defer_store_writes_only_on_delivery(self) -> None:
        self.assertTrue(defer_turn_store_enabled())
        with tempfile.TemporaryDirectory() as tmp:
            store = BehaviorStore(base_dir=tmp)
            uid = "u1"
            gen = store.bump_turn_generation(uid, None)
            pending = {
                "user_payload": "q",
                "draft_assistant": "draft",
                "dialogue_patch": {},
                "group_patch": {},
                "telegram_is_admin": False,
                "turn_meta": {},
            }
            rec = store.load(uid, None)
            self.assertFalse(rec.get("recent_messages"))
            ok = persist_turn_after_delivery(
                behavior_store=store,
                goal_engine=None,
                user_id=uid,
                group_id=None,
                assistant_text="final answer",
                pending=pending,
                generation=gen,
            )
            self.assertTrue(ok)
            rec2 = store.load(uid, None)
            self.assertEqual(rec2["recent_messages"][-1]["text"], "final answer")

    def test_must_blocks_helper(self) -> None:
        blocks = must_blocks_from_context(
            {"thread_action": ACTION_CORRECT},
            context={"turn_meaning": {"thread_action": ACTION_CORRECT}},
        )
        self.assertIn("user_correction", blocks)


if __name__ == "__main__":
    unittest.main()
