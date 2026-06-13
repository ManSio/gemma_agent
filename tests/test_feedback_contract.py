"""Контракт 👎: anchor нити, не эллипсис «почему так»."""
from __future__ import annotations

import unittest

from core.feedback_contract import (
    brain_addon_for_context,
    lesson_applies_in_context,
    rating_failure_class,
    rating_lesson_trigger,
    resolve_anchor_user_q,
)


class TestFeedbackContract(unittest.TestCase):
    def test_resolve_anchor_from_recent_messages(self) -> None:
        rec = {
            "recent_messages": [
                {"role": "user", "text": "Почему земля круглая и как это доказали?"},
                {"role": "assistant", "text": "Земля почти сферическая из-за гравитации."},
                {"role": "user", "text": "почему так произошло?"},
            ]
        }
        anchor = resolve_anchor_user_q(rec, "почему так произошло?")
        self.assertIn("земля", anchor.lower())

    def test_rating_lesson_trigger_uses_anchor_not_ellipsis(self) -> None:
        trig, use_regex = rating_lesson_trigger(
            rated_user_text="почему так произошло?",
            anchor_user_q="Почему земля круглая и как это доказали?",
        )
        self.assertFalse(use_regex)
        self.assertIn("земля", trig.lower())
        self.assertNotIn("почему так", trig.lower())

    def test_thread_followup_failure_class(self) -> None:
        fc = rating_failure_class(
            "почему так произошло?",
            "Почему земля круглая и как это доказали?",
            intent="explain",
        )
        self.assertEqual(fc, "thread_followup_drift")

    def test_legacy_generic_lesson_blocked_on_immediate_followup(self) -> None:
        long_a = (
            "Земля почти сферическая: гравитация сжимает массу планеты в шар "
            "на протяжении миллиардов лет формирования."
        )
        lesson = {
            "instruction": "исправь подход: не уходи в meta",
            "meta": {"source": "rating"},
        }
        ctx = {
            "discourse_resolution": {
                "last_user_q": "Почему земля круглая и как это доказали?",
            },
            "recent_dialogue": [
                {"role": "user", "text": "Почему земля круглая и как это доказали?"},
                {"role": "assistant", "text": long_a},
            ],
            "session_task": {"last_outcome": "ok"},
        }
        self.assertFalse(
            lesson_applies_in_context(lesson, "почему так произошло?", ctx)
        )

    def test_anchor_lesson_applies_when_thread_matches(self) -> None:
        lesson = {
            "instruction": "Отвечай по теме земли.",
            "meta": {
                "anchor_user_q": "Почему земля круглая и как это доказали?",
                "failure_class": "thread_followup_drift",
            },
        }
        ctx = {
            "discourse_resolution": {
                "last_user_q": "Почему земля круглая и как это доказали?",
            },
        }
        self.assertTrue(
            lesson_applies_in_context(lesson, "почему так произошло?", ctx)
        )

    def test_brain_addon_filters_non_matching_lesson(self) -> None:
        from unittest.mock import patch

        from core.feedback_contract import brain_addon_for_context

        long_a = (
            "Земля почти сферическая: гравитация сжимает массу планеты в шар "
            "на протяжении миллиардов лет формирования."
        )
        fake_doc = {
            "version": 1,
            "lessons": [
                {
                    "id": "t1",
                    "trigger": "почему так произошло",
                    "instruction": "исправь подход: meta bad",
                    "active": True,
                },
                {
                    "id": "t2",
                    "trigger": "земля круглая",
                    "instruction": "Отвечай по физике.",
                    "active": True,
                    "meta": {
                        "anchor_user_q": "Почему земля круглая и как это доказали?",
                    },
                },
            ],
        }
        ctx = {
            "discourse_resolution": {
                "last_user_q": "Почему земля круглая и как это доказали?",
            },
            "recent_dialogue": [
                {"role": "user", "text": "Почему земля круглая и как это доказали?"},
                {"role": "assistant", "text": long_a},
            ],
            "session_task": {"last_outcome": "ok"},
        }
        with patch("core.ephemeral_lessons.load_document", return_value=fake_doc):
            addon = brain_addon_for_context("почему так произошло?", ctx)
        self.assertIn("физике", addon.lower())
        self.assertNotIn("meta bad", addon.lower())


if __name__ == "__main__":
    unittest.main()
