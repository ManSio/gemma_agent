"""Интеграция prod-сценария Wratmak: stale weather + prior clarify + correction."""
from __future__ import annotations

import unittest

from core.brain.discourse_resolver import ACTION_CORRECT, resolve_discourse
from core.dialogue_slots import SLOT_WEATHER_CITY, get_active_slot, set_slot
from core.reply_mode_footer import build_mode_footer_fields
from core.turn_reconcile import apply_discourse_and_collapse_sync
from core.turn_state import collapse_turn_state


class ProdThreadWratmakTests(unittest.TestCase):
    def test_stale_weather_cleared_philosophy_footer_empty(self) -> None:
        rec: dict = {}
        set_slot(rec, SLOT_WEATHER_CITY, {}, turns=3)
        ctx = {
            "recent_dialogue": [{"role": "assistant", "text": "Какой именно город вас интересует?"}],
            "discourse_resolution": {"action": "branch", "reason": "substantive_question"},
        }
        _, tsv, _ = collapse_turn_state(
            "почему название ии не соответствует сегодняшним реалям",
            ctx,
            persisted=rec,
        )
        self.assertIsNone(get_active_slot(rec))
        fields = build_mode_footer_fields(
            route_context={"turn_state_audit": tsv.to_audit(), "route_intent": "explain"},
            persisted=rec,
        )
        self.assertNotIn("погода", fields.get("human", "").lower())

    def test_prior_clarify_short_reply_is_correct(self) -> None:
        user = "я про другое"
        prev_u = "почему название ии не соответствует сегодняшним реалям"
        last_a = (
            "Из системы: вас зовут Михаил, вы из аг. Михановичи, кошка Мурза, "
            "часовой пояс Europe/Minsk."
        )
        rd = [
            {"role": "user", "text": prev_u},
            {"role": "assistant", "text": last_a},
            {"role": "user", "text": user},
        ]
        ctx = {
            "user_text": user,
            "recent_dialogue": rd,
            "dialogue_state": {
                "last_intent": "explain",
                "last_brain_profile": "quick_explain",
                "last_assistant_excerpt": last_a,
            },
            "session_task": {"last_outcome": "clarify"},
        }
        res = resolve_discourse(user, ctx)
        self.assertEqual(res.action, ACTION_CORRECT)
        self.assertEqual(res.reason, "prior_unsatisfactory")
        self.assertIn("почему название ии", res.last_user_q)

    def test_plan_sync_collapse_sets_flag(self) -> None:
        rec: dict = {}
        set_slot(rec, SLOT_WEATHER_CITY, {}, turns=3)
        pre_ctx: dict = {"recent_dialogue": []}
        _, out, mutated = apply_discourse_and_collapse_sync(
            "привет",
            pre_ctx,
            persisted=rec,
        )
        self.assertTrue(out.get("_turn_state_collapsed"))
        self.assertIn("turn_state", out)
        self.assertIn("turn_meaning", out)
        self.assertTrue(mutated or get_active_slot(rec) is None)

    def test_sync_meaning_prior_clarify_discourse_correct(self) -> None:
        rec: dict = {"session_task": {"last_outcome": "clarify"}}
        rd = [
            {"role": "user", "text": "почему название ии не соответствует реалиям"},
            {"role": "assistant", "text": "Из системы: вас зовут Михаил."},
        ]
        pre_ctx = {
            "recent_dialogue": rd,
            "dialogue_state": {
                "last_intent": "explain",
                "last_assistant_excerpt": "Из системы: вас зовут Михаил.",
            },
        }
        _, out, _ = apply_discourse_and_collapse_sync("я про другое", pre_ctx, persisted=rec)
        dr = out.get("discourse_resolution") or {}
        self.assertEqual(dr.get("action"), ACTION_CORRECT)
