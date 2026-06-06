"""Проверка, что generate() собирает multimodal messages для vision."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock

from core.openrouter_provider import OpenRouterProvider, reset_openrouter_provider_for_tests


class TestOpenRouterVisionPayload(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        reset_openrouter_provider_for_tests()

    async def test_generate_sends_image_url_when_vision_parts_set(self) -> None:
        prov = OpenRouterProvider(api_key="k", api_key_dev=None)
        prov.model_threshold = 999999
        prov.current_usage = 0

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(
            return_value={
                "model": "x",
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"total_tokens": 1},
            }
        )
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post = MagicMock(return_value=mock_resp)

        prov._shared_http_session = AsyncMock(return_value=mock_session)

        out = await prov.generate(
            prompt="Describe this",
            system_prompt="sys",
            model=None,
            vision_image_parts=[("image/png", "AAA")],
        )

        self.assertTrue(out.get("success"))
        self.assertEqual(out.get("content"), "ok")
        mock_session.post.assert_called_once()
        payload = mock_session.post.call_args.kwargs.get("json") or {}
        msgs = payload.get("messages") or []
        self.assertEqual(len(msgs), 2)
        user = msgs[-1]
        self.assertEqual(user.get("role"), "user")
        content = user.get("content")
        self.assertIsInstance(content, list)
        self.assertEqual(content[0], {"type": "text", "text": "Describe this"})
        self.assertEqual(content[1]["type"], "image_url")
        self.assertTrue(
            str(content[1].get("image_url", {}).get("url", "")).startswith(
                "data:image/png;base64,AAA"
            )
        )
        self.assertEqual(payload.get("model"), "google/gemini-2.0-flash-exp:free")
