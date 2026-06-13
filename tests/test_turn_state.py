"""Тесты slot_registry и TurnStateVector."""
from __future__ import annotations

import unittest

from core.dialogue_slots import SLOT_WEATHER_CITY, get_active_slot, set_slot
from core.slot_registry import (
    SLOT_ARTICLE_THREAD,
    slot_accepts_turn,
    slot_footer_label,
)
from core.turn_reconcile import turn_state_audit_for_emit
from core.turn_state import build_turn_state, collapse_turn_state


class SlotRegistryTests(unittest.TestCase):
    def test_weather_rejects_philosophy(self) -> None:
        self.assertFalse(
            slot_accepts_turn(
                SLOT_WEATHER_CITY,
                "почему название ии не соответствует реалиям",
                [],
            )
        )

    def test_weather_accepts_city(self) -> None:
        dlg = [{"role": "assistant", "text": "Какой именно город вас интересует?"}]
        self.assertTrue(slot_accepts_turn(SLOT_WEATHER_CITY, "Минск", dlg))

    def test_article_rejects_unrelated_short(self) -> None:
        self.assertFalse(slot_accepts_turn(SLOT_ARTICLE_THREAD, "я про другое", []))

    def test_footer_label(self) -> None:
        self.assertEqual(slot_footer_label(SLOT_ARTICLE_THREAD), "статья")


class TurnStateTests(unittest.TestCase):
    def test_collapse_clears_stale_weather(self) -> None:
        rec: dict = {}
        set_slot(rec, SLOT_WEATHER_CITY, {}, turns=3)
        ctx, tsv, mutated = collapse_turn_state(
            "привет как дела",
            {"recent_dialogue": [], "discourse_resolution": {"action": "branch"}},
            persisted=rec,
        )
        self.assertTrue(mutated or tsv.slot_cleared)
        self.assertIsNone(get_active_slot(rec))
        self.assertEqual(tsv.active_slot_kind, "")
        self.assertIn("turn_state", ctx)

    def test_expects_correction_from_prior_clarify(self) -> None:
        tsv = build_turn_state(
            "я про другое",
            {
                "discourse_resolution": {"action": "correct", "reason": "prior_unsatisfactory"},
                "session_task": {"last_outcome": "clarify"},
                "recent_dialogue": [
                    {"role": "user", "text": "почему название ии не соответствует"},
                    {"role": "assistant", "text": "Из системы: вас зовут Михаил"},
                ],
            },
            slot_before="",
            slot_after="",
        )
        self.assertTrue(tsv.expects_correction)


class TurnStateEmitTests(unittest.TestCase):
    def test_audit_from_plan_context(self) -> None:
        class _Plan:
            steps = [type("S", (), {"args": {"context": {"turn_state_audit": {"slot_cleared": True}}}})()]

        out = turn_state_audit_for_emit({}, _Plan())
        self.assertTrue(out and out.get("slot_cleared"))
