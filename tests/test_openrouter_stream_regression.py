"""Regression: generate_stream must not touch error_text on HTTP 200."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.openrouter_provider import OpenRouterProvider


class TestOpenRouterStreamRegression(unittest.IsolatedAsyncioTestCase):
    async def test_generate_stream_http_200_does_not_raise_error_text(self) -> None:
        """Prod 2026-06-13: status 200 fell through to error_text → llm_error fallback."""
        provider = OpenRouterProvider(api_key="sk-test")
        breaker = MagicMock()
        breaker.allow_request.return_value = True

        response = MagicMock()
        response.status = 200
        response.content.readline = AsyncMock(return_value=b"")
        response.__aenter__ = AsyncMock(return_value=response)
        response.__aexit__ = AsyncMock(return_value=None)

        session = MagicMock()
        session.post = MagicMock(return_value=response)
        session.closed = False

        with patch("core.resilience.openrouter_circuit_breaker", return_value=breaker), patch.object(
            provider, "_shared_http_session", AsyncMock(return_value=session)
        ), patch(
            "core.openrouter_provider.openrouter_llm_semaphore",
            return_value=asyncio.Semaphore(10),
        ), patch("core.openrouter_provider._openrouter_preflight_block", return_value=None):
            out = await provider.generate_stream("почему трава зеленая", max_tokens=32)

        err = str(out.get("error") or "")
        self.assertNotIn("error_text", err)
        self.assertNotIn("not associated with a value", err)
