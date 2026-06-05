"""Регрессия: direct_dialog кладёт brain_turn_telemetry в context (C6 / turns.jsonl)."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.model_profile import ModelProfile


class TestDirectDialogTelemetry(unittest.IsolatedAsyncioTestCase):
    async def test_stashes_brain_turn_telemetry_on_context(self) -> None:
        ctx: dict = {}
        prof = ModelProfile(match_label="test", temperature_first_delta=0.0)
        with (
            patch("core.brain.direct_dialog_reply._llm") as _llm,
            patch(
                "core.brain.direct_dialog_reply.with_timeout",
                new_callable=AsyncMock,
            ) as wt,
            patch("core.brain.direct_dialog_reply._persona") as persona,
            patch("core.brain.direct_dialog_reply._memory") as memory,
            patch(
                "core.telegram_stream_reply.telegram_stream_reply_enabled",
                return_value=False,
            ),
            patch(
                "core.telegram_stream_reply.telegram_stream_get_bound",
                return_value=None,
            ),
        ):
            wt.return_value = {"content": "Ответ по сути.", "usage": {"prompt_tokens": 420}}
            persona.apply_persona_to_response = MagicMock(side_effect=lambda _u, r: r)
            memory.on_after_response = AsyncMock()
            from core.brain.direct_dialog_reply import brain_direct_dialog_reply

            await brain_direct_dialog_reply(
                user_text="что такое депозит",
                user_id="u_probe",
                system_prompt="sys",
                persona={},
                memory_facts=[],
                recent_dialogue=[],
                skip_memory_writes=True,
                model_profile=prof,
                context=ctx,
                brain_profile="standard",
            )

        pack = ctx.get("brain_turn_telemetry")
        self.assertIsInstance(pack, dict)
        self.assertEqual(pack.get("prompt_tokens_est"), 420)
        self.assertGreater(int(pack.get("brain_recent_limit") or 0), 0)
        self.assertEqual(pack.get("brain_profile"), "standard")
        ds = ctx.get("dialogue_state")
        self.assertIsInstance(ds, dict)
        self.assertEqual(ds.get("brain_recent_limit"), pack.get("brain_recent_limit"))


if __name__ == "__main__":
    unittest.main()
