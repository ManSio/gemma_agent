"""Погода: профиль и «там/у меня», не устаревший weather_anchor (Лошица)."""
from __future__ import annotations

import unittest

from core.brain.text_helpers import (
    looks_like_weather_meta_question,
    task_fact_profile,
    user_text_weather_refs_saved_home,
    weather_should_use_saved_anchor,
)
from core.weather_reply import try_weather_meta_reply
from core.weather_location_store import weather_anchor_conflicts_user_facts


class TestWeatherHomeContext(unittest.TestCase):
    def test_anchor_conflicts_mikhanovichi_vs_loshitsa(self) -> None:
        facts = {"city": "Springfield, Example County, улица Советская", "country": "BY"}
        anchor = {
            "latitude": 53.84,
            "longitude": 27.55,
            "label": "Loshitsa, Minsk",
            "admin1": "Minsk",
        }
        self.assertTrue(weather_anchor_conflicts_user_facts(facts, anchor))

    def test_bare_pogoda_ignores_stale_anchor(self) -> None:
        facts = {"city": "Springfield, Example County", "country": "BY"}
        anchor = {
            "latitude": 53.84,
            "longitude": 27.55,
            "label": "Loshitsa, Minsk",
            "admin1": "Minsk",
        }
        persisted = {"weather_anchor": anchor, "user_facts": facts}
        prof = task_fact_profile("погода", facts, [], persisted=persisted)
        self.assertTrue(prof.get("is_weather"))
        self.assertFalse(prof.get("weather_use_coords"))
        self.assertIn("Springfield", prof.get("weather_geo_query") or "")

    def test_pogoda_tam_after_where_am_i(self) -> None:
        facts = {"city": "Springfield, Example County", "country": "BY"}
        dlg = [
            {"role": "user", "text": "а где я сейчас нахожусь?"},
            {
                "role": "assistant",
                "text": "По памяти ты в агрогородке Springfield, Example County.",
            },
            {"role": "user", "text": "мне нужна погода там"},
        ]
        anchor = {
            "latitude": 53.84,
            "longitude": 27.55,
            "label": "Loshitsa, Minsk",
            "admin1": "Minsk",
        }
        prof = task_fact_profile(
            "мне нужна погода там",
            facts,
            dlg,
            persisted={"weather_anchor": anchor, "user_facts": facts},
        )
        self.assertTrue(prof.get("is_weather"))
        self.assertFalse(prof.get("weather_use_coords"))
        self.assertIn("Springfield", prof.get("weather_geo_query") or "")

    def test_deictic_detection(self) -> None:
        self.assertTrue(user_text_weather_refs_saved_home("мне нужна погода там"))
        self.assertFalse(user_text_weather_refs_saved_home("погода в Минске"))

    def test_explicit_other_city_not_stale_home_anchor(self) -> None:
        facts = {"city": "Springfield", "country": "BY"}
        anchor = {
            "latitude": 53.9,
            "longitude": 27.56,
            "label": "Minsk",
            "admin1": "Minsk",
        }
        self.assertFalse(
            weather_should_use_saved_anchor(
                "погода в Минске", facts, anchor, recent_dialogue=[]
            )
        )
        prof = task_fact_profile(
            "погода в Минске",
            facts,
            [],
            persisted={"weather_anchor": anchor, "user_facts": facts},
        )
        self.assertEqual(prof.get("weather_city"), "Минск")

    def test_meta_question_not_new_forecast(self) -> None:
        self.assertTrue(looks_like_weather_meta_question("с какого района погода?"))
        prof = task_fact_profile(
            "с какого района погода?",
            {"city": "Springfield"},
            [],
            persisted={
                "weather_last_report": {
                    "place_label": "Springfield",
                    "geo_query": "Springfield, Example Region, Беларусь",
                    "admin1": "Minsk Region",
                }
            },
        )
        self.assertTrue(prof.get("is_weather_meta"))
        self.assertFalse(prof.get("is_weather"))

    def test_meta_reply_from_last_report(self) -> None:
        ans = try_weather_meta_reply(
            "с какого района погода?",
            persisted={
                "weather_last_report": {
                    "place_label": "Springfield",
                    "geo_query": "Springfield, Example Region",
                    "admin1": "Minsk Region",
                }
            },
            facts={"city": "Springfield"},
        )
        self.assertIsNotNone(ans)
        assert ans is not None
        self.assertIn("Springfield", ans)
        self.assertIn("Example Region", ans)
        self.assertNotIn("Динаров", ans)


if __name__ == "__main__":
    unittest.main()
