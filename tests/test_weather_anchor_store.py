"""Фаза 5: weather_anchor — прогноз по координатам, без дефолта «михановичи→minsk»."""
from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.brain.text_helpers import (
    brain_weather_short_circuit_requires_anchor,
    task_fact_profile,
)
from core.weather_location_store import read_weather_anchor
from core.weather_reply import try_weather_reply_sync


class TestWeatherAnchorStore(unittest.TestCase):
    def test_task_profile_uses_anchor_coords(self) -> None:
        persisted = {
            "weather_anchor": {
                "latitude": 53.87,
                "longitude": 27.52,
                "label": "Springfield",
                "admin1": "Minsk Region",
                "source": "forecast",
            },
            "user_facts": {"city": "Springfield", "country": "BY"},
        }
        prof = task_fact_profile("погода", persisted["user_facts"], None, persisted=persisted)
        self.assertTrue(prof.get("weather_use_coords"))
        self.assertAlmostEqual(float(prof["weather_lat"]), 53.87)
        self.assertNotIn("mogilev", (prof.get("weather_region_hint") or "").lower())

    def test_short_circuit_requires_anchor_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            self.assertTrue(brain_weather_short_circuit_requires_anchor())

    def test_try_weather_with_anchor_calls_coords_api(self) -> None:
        fake = {
            "configured": True,
            "summary": "Погода — Springfield, Minsk Region: +19 °C",
            "resolved": {
                "name": "Springfield",
                "latitude": 53.87,
                "longitude": 27.52,
                "admin1": "Minsk Region",
                "country": "Belarus",
            },
        }
        captured = {}

        async def _wx(**kwargs):
            captured.update(kwargs)
            return fake

        env = {
            "BRAIN_OWN_TURN_ENABLED": "true",
            "BRAIN_OWN_TURN_ALLOW_WEATHER": "false",
            "BRAIN_WEATHER_API_ENABLED": "true",
        }
        persisted = {
            "weather_anchor": {
                "latitude": 53.87,
                "longitude": 27.52,
                "label": "Springfield",
                "admin1": "Minsk Region",
            },
            "user_facts": {"city": "Springfield"},
        }
        with patch.dict(os.environ, env, clear=False):
            with patch("modules.external_apis.service.ExternalAPIService") as cls:
                inst = cls.return_value
                inst.weather_or_fallback = AsyncMock(side_effect=_wx)
                out = try_weather_reply_sync(
                    "погода",
                    persisted=persisted,
                    user_id="u1",
                )
        self.assertIsNotNone(out)
        self.assertIsNotNone(captured.get("latitude"))
        self.assertEqual(captured.get("latitude"), 53.87)

    def test_read_anchor_from_telegram_fallback(self) -> None:
        persisted = {
            "dialogue_state": {
                "last_telegram_location": {
                    "latitude": 53.1,
                    "longitude": 27.1,
                    "display_name": "Pin",
                }
            }
        }
        a = read_weather_anchor(persisted)
        self.assertIsNotNone(a)
        self.assertAlmostEqual(a["latitude"], 53.1)


if __name__ == "__main__":
    unittest.main()
