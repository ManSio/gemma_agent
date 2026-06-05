"""Погода: API в brain при выключенном planner weather_direct."""
from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

from core.brain_own_turn import brain_weather_api_enabled, planner_direct_allowed
from core.weather_reply import try_weather_reply_sync


class BrainWeatherApiPathTests(unittest.TestCase):
    def test_planner_off_api_on(self):
        env = {
            "BRAIN_OWN_TURN_ENABLED": "true",
            "BRAIN_OWN_TURN_ALLOW_WEATHER": "false",
            "BRAIN_WEATHER_API_ENABLED": "true",
        }
        with patch.dict(os.environ, env, clear=False):
            self.assertFalse(planner_direct_allowed("weather"))
            self.assertTrue(brain_weather_api_enabled())

    def test_try_weather_works_when_planner_off(self):
        fake = {"configured": True, "summary": "Погода в Минске: +3°C."}

        async def _wx(city: str = "", country: str = "", **kwargs):
            return fake

        env = {
            "BRAIN_OWN_TURN_ENABLED": "true",
            "BRAIN_OWN_TURN_ALLOW_WEATHER": "false",
            "BRAIN_WEATHER_API_ENABLED": "true",
        }
        with patch.dict(os.environ, env, clear=False):
            with patch("modules.external_apis.service.ExternalAPIService") as cls:
                inst = cls.return_value
                inst.weather_or_fallback = AsyncMock(side_effect=_wx)
                out = try_weather_reply_sync("погода в минске", persisted={"user_facts": {}})
        self.assertIsNotNone(out)
        self.assertIn("Минск", out or "")


if __name__ == "__main__":
    unittest.main()
