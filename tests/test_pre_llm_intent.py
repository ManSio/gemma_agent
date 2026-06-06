"""P3: pre-LLM shortcut lanes."""
from __future__ import annotations

import unittest

from core.intent_heuristics import detect_pre_llm_shortcut


class TestPreLlmIntent(unittest.TestCase):
    def test_weather_followup_lane(self) -> None:
        rec: dict = {}
        from core.dialogue_slots import on_assistant_reply

        on_assistant_reply(
            rec,
            "Какой именно город вас интересует?",
            user_text="погода",
        )
        lane = detect_pre_llm_shortcut(
            "Минск",
            recent_dialogue=[
                {"role": "user", "text": "Какая погода"},
                {"role": "assistant", "text": "Какой именно город?"},
            ],
            persisted=rec,
        )
        self.assertEqual(lane, "weather_followup")

    def test_wall_clock_lane(self) -> None:
        self.assertEqual(detect_pre_llm_shortcut("Который сейчас час?"), "wall_clock")
