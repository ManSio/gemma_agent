"""Springfield и др. — область из диалога/facts, не max population в геокодере."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from core.brain.text_helpers import (
    task_fact_profile,
    weather_geo_query_for_api,
    weather_region_hint_resolve,
)
from modules.external_apis.clients import WeatherAPIClient, _pick_geo_result


class TestWeatherRegionDisambig(unittest.TestCase):
    def test_dialogue_minsk_region_for_short_pogoda(self) -> None:
        dlg = [
            {"role": "user", "text": "Я в минской области"},
            {"role": "assistant", "text": "Понял."},
            {"role": "user", "text": "погода"},
        ]
        facts = {"city": "Springfield", "country": "BY"}
        prof = task_fact_profile("погода", facts, dlg)
        self.assertTrue(prof.get("is_weather"))
        self.assertEqual(prof.get("weather_region_hint"), "minsk")
        self.assertIn("Example Region", prof.get("weather_geo_query") or "")

    def test_geo_query_includes_region(self) -> None:
        q, h = weather_geo_query_for_api("Springfield", "BY", "minsk")
        self.assertEqual(h, "minsk")
        self.assertIn("Springfield", q)
        self.assertIn("Example Region", q)

    def test_ag_prefix_city_extract(self) -> None:
        from core.brain.text_helpers import weather_city_extract_from_message_only

        c, _ = weather_city_extract_from_message_only("а.г. Springfield")
        self.assertIn("springfield", c.lower())

    def test_ag_mikhanovichi_region_hint(self) -> None:
        from core.brain.text_helpers import (
            canonical_user_city_fact,
            weather_region_hint_from_text,
        )

        self.assertEqual(weather_region_hint_from_text("полгода в а.г. Springfield"), "minsk")
        self.assertEqual(
            canonical_user_city_fact("Springfield", "полгода в а.г. Springfield"),
            "аг. Springfield, Example County",
        )
        self.assertEqual(
            canonical_user_city_fact("Springfield", "погода в а.г.Springfield"),
            "аг. Springfield, Example County",
        )

    def test_weather_in_ag_without_space(self) -> None:
        from core.brain.text_helpers import weather_city_extract_from_message_only

        c, _ = weather_city_extract_from_message_only("погода в а.г.Springfield")
        self.assertIn("springfield", c.lower())

    def test_pick_geo_prefers_minsk_over_mogilev(self) -> None:
        results = [
            {
                "name": "Mikhanavichy",
                "latitude": 53.5,
                "longitude": 30.1,
                "country": "Belarus",
                "country_code": "BY",
                "admin1": "Mogilev Region",
                "population": 5000,
            },
            {
                "name": "Mikhanavichy",
                "latitude": 53.87,
                "longitude": 27.52,
                "country": "Belarus",
                "country_code": "BY",
                "admin1": "Minsk Region",
                "population": 800,
            },
        ]
        pick = _pick_geo_result(results, country="BY", admin1_hint="minsk")
        self.assertIsNotNone(pick)
        self.assertIn("Minsk", str(pick.get("admin1") or ""))


class TestWeatherOpenMeteoAdminHint(unittest.IsolatedAsyncioTestCase):
    async def test_get_current_uses_admin1_hint(self) -> None:
        geo = {
            "results": [
                {
                    "name": "Mikhanavichy",
                    "latitude": 53.5,
                    "longitude": 30.1,
                    "country": "Belarus",
                    "country_code": "BY",
                    "admin1": "Mogilev Region",
                    "population": 9000,
                },
                {
                    "name": "Mikhanavichy",
                    "latitude": 53.87,
                    "longitude": 27.52,
                    "country": "Belarus",
                    "country_code": "BY",
                    "admin1": "Minsk Region",
                    "population": 500,
                },
            ]
        }
        fc = {
            "timezone_abbreviation": "MSK",
            "hourly": {"time": [], "temperature_2m": [], "weather_code": []},
            "daily": {
                "time": ["2026-05-25"],
                "weather_code": [3],
                "temperature_2m_max": [19.0],
                "temperature_2m_min": [9.0],
            },
            "current": {
                "time": "2026-05-25T12:00",
                "temperature_2m": 19.0,
                "relative_humidity_2m": 49,
                "apparent_temperature": 19.0,
                "weather_code": 3,
                "wind_speed_10m": 28.0,
            },
        }
        with patch(
            "modules.external_apis.clients._http_get_json",
            side_effect=[(200, geo), (200, fc)],
        ):
            w = WeatherAPIClient()
            out = await w.get_current(
                city="Springfield, Example Region, Беларусь",
                country="BY",
                admin1_hint="minsk",
            )
        self.assertTrue(out.get("configured"))
        self.assertIn("Minsk", out.get("summary", ""))


if __name__ == "__main__":
    unittest.main()
