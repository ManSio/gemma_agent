"""Детерминированный recall: первое сообщение / темы (pre_llm_plan)."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from core.intent_heuristics import detect_pre_llm_shortcut
from core.memory_recall_facade import (
    build_session_meta_recall_reply,
    plain_text_requests_session_meta_recall,
)
from core.pre_llm_plan import try_pre_llm_direct_plan


class TestSessionMetaRecall(unittest.TestCase):
    def test_marker_owner_question(self) -> None:
        q = "напиши первое сообщения которое ты помнишь или темы разговора"
        self.assertTrue(plain_text_requests_session_meta_recall(q))
        self.assertEqual(detect_pre_llm_shortcut(q), "session_meta_recall")

    def test_build_reply_uses_archive_and_session_first(self) -> None:
        items = [
            {"role": "user", "text": "какие новости", "telegram_ts": 1},
            {"role": "assistant", "text": "ok", "telegram_ts": 2},
            {"role": "user", "text": "почему трава зеленая", "telegram_ts": 3},
        ]
        with patch("core.message_archive.load_message_archive_items", return_value=items):
            out = build_session_meta_recall_reply(
                user_id="123456789",
                group_id=None,
                context={"session_first_user_text": "почему трава зеленая"},
            )
        self.assertIn("какие новости", out)
        self.assertIn("почему трава зеленая", out)
        self.assertIn("session_first_user_text", out)
        self.assertNotIn("Привет", out)

    def test_pre_llm_plan_direct(self) -> None:
        with patch("core.message_archive.load_message_archive_items", return_value=[]):
            got = try_pre_llm_direct_plan(
                user_id="1",
                group_id=None,
                text="напиши первое сообщение которое помнишь",
                persisted={"session_first_user_text": "тест сессии"},
                input_meta={},
            )
        self.assertIsNotNone(got)
        self.assertEqual(got[0], "session_meta_recall_nl")
        self.assertIn("тест сессии", got[1])

    def test_disabled_by_env(self) -> None:
        with patch.dict(os.environ, {"SESSION_META_RECALL_ENABLED": "false"}, clear=False):
            self.assertEqual(
                detect_pre_llm_shortcut("напиши первое сообщение которое помнишь"),
                "",
            )


if __name__ == "__main__":
    unittest.main()
