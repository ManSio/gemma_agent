import os
import unittest
from unittest.mock import patch

from core.memory_recall_facade import (
    auto_memory_recall_enabled,
    build_pipeline_memory_addon,
    self_model_suggests_memory_boost,
    user_text_needs_dialogue_archive_context,
    user_text_suggests_broad_context_recall,
)


class MemoryRecallFacadeTests(unittest.TestCase):
    def test_broad_marker(self):
        self.assertTrue(user_text_suggests_broad_context_recall("Продолжай с того места"))
        self.assertFalse(user_text_suggests_broad_context_recall("Сколько будет 2+2"))

    def test_archive_context_marker(self):
        q = "напиши первое сообщение которое помнишь или темы разговора"
        self.assertTrue(user_text_needs_dialogue_archive_context(q))

    def test_pipeline_injects_session_meta_when_memory_question(self):
        items = [{"role": "user", "text": "какие новости", "telegram_ts": 1}]
        with patch("core.message_archive.load_message_archive_items", return_value=items):
            out = build_pipeline_memory_addon(
                user_text="напиши первое сообщение которое помнишь",
                user_id="1",
                group_id=None,
                context={"session_first_user_text": "тест"},
                recent_dialogue=[{"role": "user", "text": "кредит"}],
                user_facts={},
                telegram_message_unix=None,
            )
        self.assertIn("MemoryRecallFacade", out)
        self.assertIn("какие новости", out)

    def test_relative_takes_precedence_over_auto(self):
        with patch("core.memory_recall_facade.build_relative_time_archive_hint_for_llm", return_value="REL_BLOCK"):
            with patch.dict(os.environ, {"MEMORY_RECALL_AUTO_ENABLED": "true"}, clear=False):
                out = build_pipeline_memory_addon(
                    user_text="что писал вчера утром",
                    user_id="1",
                    group_id=None,
                    context={},
                    recent_dialogue=[{"role": "user", "text": "x"}],
                    user_facts={"timezone": "Europe/Minsk"},
                    telegram_message_unix=1_700_000_000,
                )
        self.assertIn("REL_BLOCK", out)
        self.assertNotIn("MemoryRecallFacade", out)

    def test_auto_pack_when_thin_and_enabled(self):
        with patch("core.memory_recall_facade.build_relative_time_archive_hint_for_llm", return_value=""):
            with patch("core.message_archive.load_message_archive_items", return_value=[{"role": "user", "text": "old", "telegram_ts": 1}]):
                with patch(
                    "core.dialog_memory_recall.read_session_digest_for_user",
                    return_value="",
                ):
                    with patch.dict(os.environ, {"MEMORY_RECALL_AUTO_ENABLED": "true"}, clear=False):
                        out = build_pipeline_memory_addon(
                            user_text="продолжай как раньше",
                            user_id="42",
                            group_id=None,
                            context={"dialogue_summary": "кратко про тему"},
                            recent_dialogue=[{"role": "user", "text": "a"}],
                            user_facts={},
                            telegram_message_unix=None,
                        )
        self.assertIn("MemoryRecallFacade", out)
        self.assertIn("dialogue_summary", out.lower())

    def test_auto_respects_env(self):
        with patch.dict(os.environ, {"MEMORY_RECALL_AUTO_ENABLED": "true"}, clear=False):
            self.assertTrue(auto_memory_recall_enabled())
        with patch.dict(os.environ, {"MEMORY_RECALL_AUTO_ENABLED": "false"}, clear=False):
            self.assertFalse(auto_memory_recall_enabled())

    def test_self_model_boost_thin_pack(self):
        ctx = {
            "dialogue_summary": "тема",
            "self_model": {"dynamic": {"clarify_rate": 0.6, "context_stability": 0.9}},
        }
        with patch("core.memory_recall_facade.build_relative_time_archive_hint_for_llm", return_value=""):
            with patch("core.memory_recall_facade.autonomy_extended_enabled", return_value=True):
                with patch("core.message_archive.load_message_archive_items", return_value=[{"role": "user", "text": "z", "telegram_ts": 1}]):
                    with patch("core.dialog_memory_recall.read_session_digest_for_user", return_value=""):
                        with patch.dict(
                            os.environ,
                            {"MEMORY_RECALL_SELF_MODEL_BOOST_ENABLED": "true"},
                            clear=False,
                        ):
                            out = build_pipeline_memory_addon(
                                user_text="просто вопрос",
                                user_id="7",
                                group_id=None,
                                context=ctx,
                                recent_dialogue=[{"role": "user", "text": "a"}],
                                user_facts={},
                                telegram_message_unix=None,
                            )
        self.assertIn("MemoryRecallFacade", out)

    def test_self_model_boost_skips_when_facade_already_added(self):
        ctx = {"self_model": {"dynamic": {"clarify_rate": 1.0, "context_stability": 0.0}}}
        with patch("core.memory_recall_facade.build_relative_time_archive_hint_for_llm", return_value=""):
            with patch("core.memory_recall_facade.autonomy_extended_enabled", return_value=True):
                with patch("core.message_archive.load_message_archive_items", return_value=[{"role": "user", "text": "z", "telegram_ts": 1}]):
                    with patch("core.dialog_memory_recall.read_session_digest_for_user", return_value=""):
                        with patch.dict(
                            os.environ,
                            {
                                "MEMORY_RECALL_AUTO_ENABLED": "true",
                                "MEMORY_RECALL_SELF_MODEL_BOOST_ENABLED": "true",
                            },
                            clear=False,
                        ):
                            out = build_pipeline_memory_addon(
                                user_text="продолжай",
                                user_id="7",
                                group_id=None,
                                context=ctx,
                                recent_dialogue=[{"role": "user", "text": "a"}],
                                user_facts={},
                                telegram_message_unix=None,
                            )
        self.assertEqual(out.count("(MemoryRecallFacade)"), 1)

    def test_self_model_boost_helper(self):
        with patch.dict(os.environ, {"MEMORY_RECALL_SELF_MODEL_BOOST_ENABLED": "true"}, clear=False):
            with patch("core.memory_recall_facade.autonomy_extended_enabled", return_value=True):
                self.assertTrue(
                    self_model_suggests_memory_boost(
                        {"self_model": {"dynamic": {"clarify_rate": 0.4, "context_stability": 1.0}}}
                    )
                )
                self.assertTrue(
                    self_model_suggests_memory_boost(
                        {"self_model": {"dynamic": {"clarify_rate": 0.0, "context_stability": 0.4}}}
                    )
                )
        self.assertFalse(self_model_suggests_memory_boost({}))


if __name__ == "__main__":
    unittest.main()
