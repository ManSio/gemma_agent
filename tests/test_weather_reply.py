import os
import unittest
from unittest.mock import AsyncMock, patch

from core.weather_reply import try_weather_reply_sync

_LEGACY_WEATHER_ENV = {
    "BRAIN_OWN_TURN_ENABLED": "true",
    "BRAIN_OWN_TURN_ALLOW_WEATHER": "true",
}


class WeatherReplyTests(unittest.TestCase):
    def test_minsk_direct_open_meteo(self):
        fake = {
            "configured": True,
            "summary": "Погода в Минске: +5°C, облачно.",
        }

        async def _wx(city: str = "", country: str = "", **kwargs):
            return fake

        with patch.dict(os.environ, _LEGACY_WEATHER_ENV, clear=False):
            with patch("modules.external_apis.service.ExternalAPIService") as cls:
                inst = cls.return_value
                inst.weather_or_fallback = AsyncMock(side_effect=_wx)
                out = try_weather_reply_sync("погода в минске", persisted={"user_facts": {}})
        self.assertIsNotNone(out)
        self.assertIn("Минск", out or "")

    def test_non_weather_returns_none(self):
        out = try_weather_reply_sync("привет", persisted={"user_facts": {}})
        self.assertIsNone(out)

    def test_bare_pogoda_without_place_returns_none(self):
        """«Погода» без города/якоря — в brain, не заглушка «Напишите город»."""
        with patch.dict(os.environ, _LEGACY_WEATHER_ENV, clear=False):
            out = try_weather_reply_sync("Погода", persisted={"user_facts": {}})
        self.assertIsNone(out)


if __name__ == "__main__":
    unittest.main()
