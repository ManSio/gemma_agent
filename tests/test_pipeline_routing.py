"""Регрессия: pipeline_routing preflight и audit."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from core.brain.pipeline_routing import resolve_brain_route


class PipelineRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_preflight_url_to_summarization(self) -> None:
        ctx: dict = {"dialogue_state": {"last_intent": "general"}}
        with patch.dict(
            "os.environ",
            {"BRAIN_ROUTER_OVERRIDE_MIN_CONFIDENCE": "0.85"},
            clear=False,
        ):
            res = await resolve_brain_route(
                "https://habr.com/ru/articles/123456/",
                ctx,
                llm=None,
            )
        self.assertEqual(res.brain_profile, "summarization")
        self.assertEqual(ctx.get("brain_profile"), "summarization")
        ra = ctx.get("router_route_audit") or {}
        self.assertEqual(ra.get("preflight"), "summarization")

    async def test_continuation_cannot_keep_math_on_habr_url(self) -> None:
        """После «Продолжи» не наследовать math_solve на URL-only Habr."""
        ctx: dict = {
            "dialogue_state": {
                "last_intent": "math",
                "last_brain_profile": "math_solve",
            },
        }
        res = await resolve_brain_route(
            "Продолжи\nhttps://habr.com/ru/articles/1036002/",
            ctx,
            llm=None,
        )
        self.assertEqual(res.brain_profile, "summarization")


if __name__ == "__main__":
    unittest.main()
