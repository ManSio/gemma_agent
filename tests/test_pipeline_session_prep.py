"""Регрессия: pipeline_session_prep KV debug и memory recall guard."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from core.brain.pipeline_session_prep import setup_early_brain_session


class PipelineSessionPrepTests(unittest.TestCase):
    def test_kv_debug_written_to_context(self) -> None:
        ctx: dict = {"recent_messages": []}
        with patch(
            "core.brain.pipeline_session_prep._resolve_sticky_session",
            return_value=("sess-abc", {"session_id": "sess-abc", "epoch": 1}),
        ), patch(
            "core.safety_config.kv_session_reset_enabled",
            return_value=False,
        ):
            sid, dbg = setup_early_brain_session(
                user_id="u1",
                user_text="привет",
                context=ctx,
                brain_profile="short",
                dialogue_state={"last_intent": "general"},
            )
        self.assertEqual(sid, "sess-abc")
        self.assertEqual(ctx["kv_session_debug"]["session_id"], "sess-abc")
        self.assertEqual(dbg["epoch"], 1)

    def test_session_id_epoch_suffix_when_kv_reset_enabled(self) -> None:
        ctx: dict = {"recent_messages": []}
        with patch(
            "core.brain.pipeline_session_prep._resolve_sticky_session",
            return_value=("sess-abc", {"session_id": "sess-abc", "epoch": 1}),
        ), patch(
            "core.safety_config.kv_session_reset_enabled",
            return_value=True,
        ), patch(
            "core.dialog_state.get_kv_session_epoch",
            return_value=1,
        ):
            sid, _dbg = setup_early_brain_session(
                user_id="u1",
                user_text="привет",
                context=ctx,
                brain_profile="short",
                dialogue_state={"last_intent": "general"},
            )
        self.assertEqual(sid, "sess-abc.ds1")

    def test_memory_recall_disabled_flag(self) -> None:
        ctx: dict = {"recent_messages": []}
        with patch(
            "core.brain.pipeline_session_prep._resolve_sticky_session",
            return_value=("", {}),
        ), patch(
            "core.memory_recall.memory_recall_allowed",
            return_value=False,
        ):
            setup_early_brain_session(
                user_id="u1",
                user_text="что я говорил вчера",
                context=ctx,
                brain_profile="standard",
                dialogue_state={},
            )
        self.assertTrue(ctx.get("memory_recall_disabled"))


if __name__ == "__main__":
    unittest.main()
