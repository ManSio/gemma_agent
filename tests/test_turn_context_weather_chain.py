"""Многоходовая цепочка погоды — как в Telegram, один persisted."""
from __future__ import annotations

import unittest

from core.brain.text_helpers import task_fact_profile
from core.turn_context import build_turn_context, prepare_persisted_for_weather
from core.weather_location_store import weather_anchor_conflicts_user_facts


def _mikhanovichi_persisted() -> dict:
    bad_anchor = {
        "latitude": 53.84,
        "longitude": 27.55,
        "label": "Loshitsa, Minsk",
        "admin1": "Minsk",
    }
    facts = {
        "city": "Springfield, Example County, улица Советская",
        "country": "BY",
    }
    return {
        "user_facts": facts,
        "weather_anchor": bad_anchor,
        "recent_messages": [],
    }


class TestTurnContextWeatherChain(unittest.TestCase):
    def test_step1_pogoda_not_loshitsa(self) -> None:
        persisted = _mikhanovichi_persisted()
        facts = persisted["user_facts"]
        self.assertTrue(weather_anchor_conflicts_user_facts(facts, persisted["weather_anchor"]))
        prepare_persisted_for_weather(persisted, facts)
        tc = build_turn_context("погода", persisted)
        self.assertTrue(tc.is_weather)
        self.assertFalse(tc.weather_use_coords)
        self.assertIn("Springfield", tc.weather_geo_query)

    def test_step2_where_then_pogoda_tam(self) -> None:
        persisted = _mikhanovichi_persisted()
        facts = persisted["user_facts"]
        dlg = [
            {"role": "user", "text": "а где я сейчас нахожусь?"},
            {
                "role": "assistant",
                "text": "По памяти ты в агрогородке Springfield, Example County.",
            },
        ]
        persisted["recent_messages"] = dlg
        prepare_persisted_for_weather(persisted, facts)
        tc = build_turn_context("мне нужна погода там", persisted)
        self.assertTrue(tc.is_weather)
        self.assertFalse(tc.weather_use_coords)
        self.assertIn("Springfield", tc.weather_geo_query)
        self.assertNotIn("Loshitsa", tc.weather_geo_query)

    def test_step3_explicit_ag_matches_profile(self) -> None:
        persisted = _mikhanovichi_persisted()
        prof = task_fact_profile(
            "какая погода в аг.Springfield",
            persisted["user_facts"],
            [],
            persisted=persisted,
        )
        self.assertTrue(prof.get("is_weather"))
        gq = (prof.get("weather_geo_query") or "").lower()
        self.assertTrue("springfield" in gq or "example" in gq)
        self.assertNotIn("loshitsa", gq)
        self.assertNotIn("лошиц", gq)


if __name__ == "__main__":
    unittest.main()
