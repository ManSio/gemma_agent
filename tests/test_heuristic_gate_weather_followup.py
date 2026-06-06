"""Gate weather_direct: короткий город после уточнения не блокируется."""
from __future__ import annotations

import unittest

from core.dialogue_slots import on_assistant_reply
from core.heuristic_context_gate import should_run_shortcut


class TestHeuristicGateWeatherFollowup(unittest.TestCase):
    def test_minsk_after_weather_clarify_allowed(self) -> None:
        rec: dict = {}
        on_assistant_reply(
            rec,
            "Какой именно город вас интересует? Назовите населённый пункт.",
            user_text="Какая погода",
        )
        recent = [
            {"role": "user", "text": "Какая погода"},
            {"role": "assistant", "text": "Какой именно город?"},
        ]
        gr = should_run_shortcut(
            "weather_direct",
            "Минск",
            persisted=rec,
            planner_context={"recent_dialogue": recent},
        )
        self.assertTrue(gr.allowed, f"expected allowed got {gr.verdict} {gr.reason}")
