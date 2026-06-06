"""Флаг operational_diag_short_circuit в context после раннего ответа pipeline."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from core.brain.pipeline import call_brain


class BrainOperationalShortCircuitMetaTests(unittest.TestCase):
    def test_call_brain_sets_context_flag(self):
        async def run():
            ctx = {
                "user_id": "operational-meta-test",
                "brain_skip_memory_fetch": True,
                "memory_managed": True,
                "mem0_facts": [],
            }
            with patch(
                "core.brain.pipeline._persona_apply_polished",
                side_effect=lambda uid, r: r,
            ):
                reply = await call_brain("проверь баланс openrouter", ctx, "sys")
            return reply, ctx

        reply, ctx = asyncio.run(run())
        self.assertTrue(ctx.get("operational_diag_short_circuit"))
        self.assertIn("OpenRouter", reply or "")


if __name__ == "__main__":
    unittest.main()
