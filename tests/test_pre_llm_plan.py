"""P3: orchestrator.plan direct_reply до brain."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from core.intent_heuristics import detect_pre_llm_shortcut
from core.orchestrator import _FALLBACK_DIRECT_REPLY_VARIANTS
from core.pre_llm_plan import PRE_LLM_DIRECT_VARIANTS, try_pre_llm_direct_plan
from core.timezone_inference import try_wall_clock_direct_reply


class TestPreLlmPlan(unittest.TestCase):
    def test_wall_clock_direct_reply(self) -> None:
        out = try_wall_clock_direct_reply(
            "Который сейчас час?",
            user_facts={"timezone": "Europe/Minsk", "city": "Минск"},
        )
        self.assertIn("Сейчас", out)
        self.assertIn("минск", out.lower())

    def test_pre_llm_plan_wall_clock(self) -> None:
        got = try_pre_llm_direct_plan(
            user_id="1",
            group_id=None,
            text="Сколько сейчас времени?",
            persisted={"user_facts": {"timezone": "Europe/Minsk"}},
            input_meta={},
        )
        self.assertIsNotNone(got)
        reason, reply = got
        self.assertEqual(reason, "wall_clock_direct")
        self.assertIn("Сейчас", reply)

    def test_pre_llm_plan_session_meta_before_dialog_recall(self) -> None:
        q = "напиши первое сообщения которое ты помнишь или темы разговора"
        self.assertEqual(detect_pre_llm_shortcut(q), "session_meta_recall")
        with patch("core.message_archive.load_message_archive_items", return_value=[]):
            got = try_pre_llm_direct_plan(
                user_id="1",
                group_id=None,
                text=q,
                persisted={"session_first_user_text": "сессия"},
                input_meta={},
            )
        self.assertIsNotNone(got)
        self.assertEqual(got[0], "session_meta_recall_nl")

    def test_pre_llm_plan_dialog_recall_off_by_default(self) -> None:
        with patch.dict(os.environ, {"SESSION_META_RECALL_ENABLED": "false"}, clear=False):
            self.assertEqual(detect_pre_llm_shortcut("напиши первое сообщение"), "")
        self.assertEqual(detect_pre_llm_shortcut("напомни что мы обсуждали"), "")
        got = try_pre_llm_direct_plan(
            user_id="1",
            group_id=None,
            text="напомни что мы обсуждали вчера",
            persisted={},
            input_meta={},
        )
        self.assertIsNone(got)

    def test_pre_llm_plan_dialog_recall_when_enabled(self) -> None:
        with patch.dict(os.environ, {"DIALOG_RECALL_NL_ROUTE_ENABLED": "true"}, clear=False):
            with patch(
                "core.memory_recall_facade.build_slash_recall_bundle",
                return_value="Сводка переписки: …",
            ):
                got = try_pre_llm_direct_plan(
                    user_id="1",
                    group_id=None,
                    text="напомни что мы обсуждали",
                    persisted={"user_facts": {}},
                    input_meta={},
                )
        self.assertIsNotNone(got)
        self.assertEqual(got[0], "dialog_recall_nl")

    def test_pre_llm_variants_in_orchestrator_whitelist(self) -> None:
        self.assertTrue(
            PRE_LLM_DIRECT_VARIANTS <= _FALLBACK_DIRECT_REPLY_VARIANTS,
            msg=f"missing: {PRE_LLM_DIRECT_VARIANTS - _FALLBACK_DIRECT_REPLY_VARIANTS}",
        )
        self.assertIn("article_thread_followup_nl", PRE_LLM_DIRECT_VARIANTS)


if __name__ == "__main__":
    unittest.main()
