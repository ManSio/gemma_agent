"""Фаза 5: weather_anchor после commit city в user_facts."""
from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.weather_location_store import (
    _geocode_anchor_from_facts_async,
    refresh_weather_anchor_from_facts,
)


class TestWeatherAnchorFromFacts(unittest.TestCase):
    def test_geocode_anchor_from_resolved(self) -> None:
        fake = {
            "configured": True,
            "resolved": {
                "name": "Springfield",
                "latitude": 53.87,
                "longitude": 27.52,
                "admin1": "Minsk Region",
            },
        }

        async def _wx(**kwargs):
            return fake

        with patch("modules.external_apis.service.ExternalAPIService") as cls:
            inst = cls.return_value
            inst.weather_or_fallback = AsyncMock(side_effect=_wx)
            anchor = asyncio.run(
                _geocode_anchor_from_facts_async("Springfield, Example County", "BY")
            )
        self.assertIsNotNone(anchor)
        self.assertAlmostEqual(anchor["latitude"], 53.87)

    def test_refresh_applies_anchor(self) -> None:
        store = MagicMock()
        fake = {
            "configured": True,
            "resolved": {
                "name": "Минск",
                "latitude": 53.9,
                "longitude": 27.56,
                "admin1": "Minsk",
            },
        }

        async def _wx(**kwargs):
            return fake

        env = {"WEATHER_ANCHOR_ON_FACT_COMMIT": "true"}
        with patch.dict(os.environ, env, clear=False):
            with patch("modules.external_apis.service.ExternalAPIService") as cls:
                inst = cls.return_value
                inst.weather_or_fallback = AsyncMock(side_effect=_wx)
                refresh_weather_anchor_from_facts(
                    store,
                    "u1",
                    None,
                    {"city": "Минск", "country": "BY"},
                )
        store.patch_session_fields.assert_called_once()
        wa = store.patch_session_fields.call_args[0][2].get("weather_anchor")
        self.assertIsNotNone(wa)
        self.assertAlmostEqual(wa["latitude"], 53.9)


if __name__ == "__main__":
    unittest.main()
