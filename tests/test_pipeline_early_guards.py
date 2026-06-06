"""Регрессия: pipeline_early_guards."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from core.brain.constants import SILENT_IMAGE_USER_PROMPT
from core.brain.pipeline_early_guards import (
    apply_early_input_guards,
    compute_memory_skip_flags,
    resolve_user_text_with_file_context,
)


class PipelineEarlyGuardsTests(unittest.IsolatedAsyncioTestCase):
    def test_silent_image_substitutes_prompt(self) -> None:
        fc = {"file_type": "image", "local_path": "/tmp/x.jpg"}
        out = resolve_user_text_with_file_context("", fc)
        self.assertEqual(out, SILENT_IMAGE_USER_PROMPT)

    def test_need_memory_clears_skip_mem_fetch(self) -> None:
        ctx = {"brain_skip_memory_fetch": True}
        _sw, smf = compute_memory_skip_flags(ctx, need_memory=True)
        self.assertFalse(smf)

    async def test_single_glyph_early_exit(self) -> None:
        with patch(
            "core.brain.pipeline_early_guards._natural_fallback_response",
            return_value="glyph-reply",
        ), patch(
            "core.brain.pipeline_early_guards._polish_and_persist_early_reply",
            new_callable=AsyncMock,
            return_value="glyph-reply",
        ) as mock_polish:
            gate = await apply_early_input_guards(
                user_id="u1",
                user_text="а",
                context={},
                need_memory=False,
            )
        self.assertEqual(gate.early_reply, "glyph-reply")
        mock_polish.assert_awaited_once()

    async def test_empty_text_returns_empty_fallback(self) -> None:
        with patch(
            "core.brain.pipeline_early_guards._natural_fallback_response",
            return_value="empty-reply",
        ):
            gate = await apply_early_input_guards(
                user_id="u1",
                user_text="   ",
                context={},
                need_memory=False,
            )
        self.assertEqual(gate.early_reply, "empty-reply")

    async def test_pasted_article_skips_heavy_guard(self) -> None:
        paste = (
            "Тихановская выступила с обращением к гражданам. "
            "Она заявила о необходимости перемен. " * 14
            + "\n\nЧитайте также на myfin.by."
        )
        with patch(
            "core.brain.pipeline_early_guards._user_input_heavy_for_llm",
            return_value=True,
        ), patch(
            "core.brain.pipeline_early_guards._natural_fallback_response",
            return_value="should-not-run",
        ) as mock_fb:
            gate = await apply_early_input_guards(
                user_id="u1",
                user_text=paste,
                context={},
                need_memory=False,
            )
        self.assertIsNone(gate.early_reply)
        mock_fb.assert_not_called()


if __name__ == "__main__":
    unittest.main()
