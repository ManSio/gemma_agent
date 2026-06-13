"""Gate planner shortcuts через TurnMeaning до weather/geo/pre_llm."""
from __future__ import annotations

import unittest

from core.dialogue_slots import SLOT_WEATHER_CITY, set_slot
from core.turn_meaning import (
    REFERENT_AGENT,
    REFERENT_THREAD,
    REFERENT_USER,
    REFERENT_WORLD,
    SPEECH_CORRECTION,
    TurnMeaning,
    resolve_turn_meaning_structural,
)
from core.turn_shortcut_gate import (
    planner_shortcut_allowed,
    prepare_plan_turn_gate,
    weather_turn_binds_slot,
)


class TurnShortcutGateTests(unittest.TestCase):
    def test_identity_question_blocks_weather(self) -> None:
        meaning = resolve_turn_meaning_structural("как меня зовут?", {})
        self.assertEqual(meaning.referent, REFERENT_USER)
        self.assertFalse(
            planner_shortcut_allowed(
                "weather_direct",
                meaning,
            )
        )

    def test_agent_question_blocks_geo(self) -> None:
        meaning = resolve_turn_meaning_structural("какие проблемы у тебя сейчас есть?", {})
        self.assertEqual(meaning.referent, REFERENT_AGENT)
        self.assertFalse(planner_shortcut_allowed("geo_nearby", meaning))

    def test_weather_slot_bind_allows_city(self) -> None:
        rec: dict = {}
        set_slot(rec, SLOT_WEATHER_CITY, {}, turns=2)
        meaning = TurnMeaning(
            referent=REFERENT_THREAD,
            thread_action="stay",
            speech_act="continuation",
        )
        self.assertTrue(
            weather_turn_binds_slot("Минск", rec),
        )
        self.assertTrue(
            planner_shortcut_allowed(
                "weather_followup",
                meaning,
                weather_slot_bind=weather_turn_binds_slot("Минск", rec),
            )
        )

    def test_correction_blocks_weather_not_identity_lane(self) -> None:
        meaning = TurnMeaning(
            speech_act=SPEECH_CORRECTION,
            thread_action="correct",
            referent=REFERENT_WORLD,
        )
        self.assertFalse(planner_shortcut_allowed("weather_direct", meaning))
        self.assertTrue(planner_shortcut_allowed("user_facts_identity_nl", meaning))

    def test_prepare_plan_turn_gate_hydrates_session(self) -> None:
        persisted = {
            "session_task": {"last_outcome": "clarify"},
            "recent_messages": [
                {"role": "user", "text": "почему название ии"},
                {"role": "assistant", "text": "факты"},
            ],
        }
        meaning, ctx = prepare_plan_turn_gate(
            "я про другое",
            "591226766",
            None,
            persisted,
        )
        self.assertEqual(meaning.thread_action, "correct")
        self.assertIn("turn_meaning", ctx)
        self.assertIn("session_task", ctx)


if __name__ == "__main__":
    unittest.main()
