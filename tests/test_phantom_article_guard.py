"""Tests for phantom article guard and clarify expects_reply."""
from __future__ import annotations

import unittest

from core.brain.discourse_resolver import structural_thread_continuation
from core.phantom_article_guard import (
    session_requested_article_without_paste,
    should_phantom_article_guard,
    try_phantom_article_guard_reply,
)
from core.prompt_routing import infer_assistant_expects_reply


class PhantomArticleGuardTests(unittest.TestCase):
    def test_session_detects_read_without_paste(self) -> None:
        dlg = [
            {"role": "user", "text": "Прочитай статью про новые модели ИИ 2026 года"},
            {
                "role": "assistant",
                "text": "Пожалуйста, уточните, что именно вы хотите обсудить из статьи.",
            },
        ]
        topic = session_requested_article_without_paste(dlg)
        self.assertIsNotNone(topic)
        self.assertIn("стать", (topic or "").lower())

    def test_guard_blocks_ellipsis_problems(self) -> None:
        dlg = [
            {"role": "user", "text": "Прочитай статью про новые модели ИИ 2026 года"},
            {"role": "assistant", "text": "Уточните, что обсудить из статьи."},
        ]
        reply = try_phantom_article_guard_reply(
            "А какие у них реальные проблемы?",
            recent_dialogue=dlg,
        )
        self.assertIsNotNone(reply)
        assert reply is not None
        self.assertIn("текста или ссылки", reply.lower())
        self.assertNotIn("команды", reply.lower())

    def test_guard_skips_when_article_pasted(self) -> None:
        paste = "МИД Украины прокомментировал биолаборатории. " * 12
        dlg = [
            {"role": "user", "text": paste},
            {"role": "assistant", "text": "Краткий пересказ."},
        ]
        self.assertFalse(
            should_phantom_article_guard("как ты думаешь правда?", dlg)
        )


class ClarifyExpectsReplyTests(unittest.TestCase):
    def test_short_clarify_without_question_mark(self) -> None:
        clarify = (
            "Пожалуйста, уточните, что именно вы хотите обсудить "
            "из статьи про новые модели ИИ 2026 года."
        )
        self.assertTrue(
            infer_assistant_expects_reply(clarify, last_intent="explain")
        )

    def test_discourse_continuation_after_short_clarify(self) -> None:
        clarify = (
            "Пожалуйста, уточните, что именно вы хотите обсудить "
            "из статьи про новые модели ИИ 2026 года."
        )
        user = "А какие у них реальные проблемы?"
        rd = [
            {"role": "user", "text": "Прочитай статью про ИИ 2026"},
            {"role": "assistant", "text": clarify},
            {"role": "user", "text": user},
        ]
        ctx = {
            "user_text": user,
            "recent_dialogue": rd,
            "dialogue_state": {
                "last_intent": "explain",
                "last_assistant_excerpt": clarify,
            },
        }
        inherit, reason = structural_thread_continuation(user, ctx)
        self.assertTrue(inherit, reason)


if __name__ == "__main__":
    unittest.main()
