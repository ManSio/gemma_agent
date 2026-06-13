"""OpenRouter generate() records circuit breaker failures on HTTP errors."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.openrouter_provider import OpenRouterProvider


class TestOpenRouterCircuitIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_generate_records_failure_on_http_500(self) -> None:
        provider = OpenRouterProvider(api_key="sk-test")
        breaker = MagicMock()
        breaker.allow_request.return_value = True

        response = MagicMock()
        response.status = 500
        response.text = AsyncMock(return_value="server error")
        response.__aenter__ = AsyncMock(return_value=response)
        response.__aexit__ = AsyncMock(return_value=None)

        session = MagicMock()
        session.post = MagicMock(return_value=response)
        session.closed = False

        with patch("core.resilience.openrouter_circuit_breaker", return_value=breaker), patch.object(
            provider, "_shared_http_session", AsyncMock(return_value=session)
        ), patch("core.openrouter_provider._openrouter_http_retry_params", return_value=(1, 0.0)        ), patch(
            "core.openrouter_provider.openrouter_llm_semaphore",
            return_value=asyncio.Semaphore(10),
        ):
            out = await provider.generate("hello", max_tokens=10)

        self.assertIn("error", out)
        breaker.record_failure.assert_called()
