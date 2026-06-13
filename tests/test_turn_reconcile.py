"""Сверка хода: слоты + footer-метки."""
from __future__ import annotations

import unittest

from core.dialogue_slots import SLOT_WEATHER_CITY, get_active_slot, set_slot
from core.turn_reconcile import hydrate_session_task, reconcile_turn_state


class TestTurnReconcile(unittest.TestCase):
    def test_clears_weather_slot_and_sets_footer_kind(self) -> None:
        rec: dict = {}
        set_slot(rec, SLOT_WEATHER_CITY, {}, turns=3)
        ctx, mutated = reconcile_turn_state(
            "почему название ии не соответствует реалиям",
            {"recent_dialogue": []},
            persisted=rec,
        )
        self.assertTrue(mutated)
        self.assertIsNone(get_active_slot(rec))
        self.assertEqual(ctx.get("active_dialogue_slot_kind"), "")

    def test_weather_slot_survives_city_bind_turn(self) -> None:
        rec: dict = {}
        set_slot(rec, SLOT_WEATHER_CITY, {}, turns=3)
        dlg = [{"role": "assistant", "text": "Какой именно город вас интересует?"}]
        ctx, mutated = reconcile_turn_state("Минск", {"recent_dialogue": dlg}, persisted=rec)
        slot = get_active_slot(rec)
        self.assertIsNotNone(slot)
        self.assertEqual(int(slot.get("turns_left") or 0), 2)
        self.assertEqual(ctx.get("active_dialogue_slot_kind"), SLOT_WEATHER_CITY)
        self.assertTrue(mutated)

    def test_hydrate_session_task_from_persisted(self) -> None:
        ctx: dict = {}
        hydrate_session_task(
            ctx,
            {"session_task": {"last_outcome": "clarify", "last_intent": "explain"}},
        )
        self.assertEqual(ctx.get("session_task", {}).get("last_outcome"), "clarify")
