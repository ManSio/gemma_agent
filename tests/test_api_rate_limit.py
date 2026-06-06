"""Регрессия API rate limit для тяжёлых эндпоинтов."""
from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import MagicMock

from fastapi import HTTPException


class TestApiRateLimit(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from core import api_rate_limit as m

        self.m = m
        os.environ["API_RATE_LIMIT_ENABLED"] = "true"
        os.environ["API_RATE_LIMIT_HEAVY_RPM"] = "3"
        os.environ["API_RATE_LIMIT_HEAVY_MIN_INTERVAL_SEC"] = "0"
        async with m._lock:
            m._window_events.clear()
            m._last_call.clear()

    def _request(self, host: str = "127.0.0.1") -> MagicMock:
        req = MagicMock()
        req.client.host = host
        req.headers.get.return_value = ""
        return req

    async def test_allows_under_rpm(self) -> None:
        req = self._request()
        for _ in range(3):
            await self.m.assert_api_heavy_rate_limit(req, user_id="u1")

    async def test_blocks_fourth_in_window(self) -> None:
        req = self._request()
        for _ in range(3):
            await self.m.assert_api_heavy_rate_limit(req, user_id="u1")
        with self.assertRaises(HTTPException) as ctx:
            await self.m.assert_api_heavy_rate_limit(req, user_id="u1")
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertIn("Retry-After", ctx.exception.headers or {})

    async def test_min_interval(self) -> None:
        os.environ["API_RATE_LIMIT_HEAVY_RPM"] = "100"
        os.environ["API_RATE_LIMIT_HEAVY_MIN_INTERVAL_SEC"] = "60"
        async with self.m._lock:
            self.m._window_events.clear()
            self.m._last_call.clear()
        req = self._request()
        await self.m.assert_api_heavy_rate_limit(req, user_id="u2")
        with self.assertRaises(HTTPException) as ctx:
            await self.m.assert_api_heavy_rate_limit(req, user_id="u2")
        self.assertEqual(ctx.exception.status_code, 429)

    async def test_disabled(self) -> None:
        os.environ["API_RATE_LIMIT_ENABLED"] = "false"
        req = self._request()
        for _ in range(10):
            await self.m.assert_api_heavy_rate_limit(req, user_id="u3")


if __name__ == "__main__":
    unittest.main()
