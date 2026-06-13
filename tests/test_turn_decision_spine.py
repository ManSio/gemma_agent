"""Post-reconcile decision spine: ephemeral после discourse, profile lock."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from core.turn_decision_spine import (
    apply_meaning_profile_lock,
    ephemeral_lessons_hint_for_context,
    refresh_post_reconcile_payload,
)
from core.turn_meaning import (
    REFERENT_AGENT,
    apply_turn_meaning_to_context,
    resolve_turn_meaning_structural,
)


class TurnDecisionSpineTests(unittest.TestCase):
    def test_refresh_sets_spine_flag_and_lock(self) -> None:
        long_a = (
            "Земля почти сферическая: гравитация сжимает массу планеты в шар "
            "на протяжении миллиардов лет формирования."
        )
        ctx = apply_turn_meaning_to_context(
            {
                "discourse_resolution": {
                    "last_user_q": "Почему земля круглая и как это доказали?",
                },
                "recent_dialogue": [
                    {"role": "user", "text": "Почему земля круглая и как это доказали?"},
                    {"role": "assistant", "text": long_a},
                ],
                "session_task": {"last_outcome": "ok"},
            },
            resolve_turn_meaning_structural("какие проблемы у тебя сейчас есть?", {}),
        )
        out = refresh_post_reconcile_payload(ctx, "какие проблемы у тебя сейчас есть?")
        self.assertTrue(out.get("post_reconcile_spine_ready"))
        self.assertEqual(out.get("meaning_profile_lock"), "standard")

    def test_stale_ephemeral_not_used_when_spine_filters(self) -> None:
        fake_doc = {
            "version": 1,
            "lessons": [
                {
                    "id": "legacy",
                    "trigger": "почему так произошло",
                    "instruction": "исправь подход: meta bad",
                    "active": True,
                },
            ],
        }
        long_a = (
            "Земля почти сферическая: гравитация сжимает массу планеты в шар "
            "на протяжении миллиардов лет формирования."
        )
        ctx = {
            "ephemeral_lessons_brain_addon": "Временные правки:\n- meta bad",
            "post_reconcile_spine_ready": True,
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
            refresh_post_reconcile_payload(ctx, "почему так произошло?")
        hint = ephemeral_lessons_hint_for_context(ctx, "почему так произошло?")
        self.assertNotIn("meta bad", hint.lower())

    def test_meaning_profile_lock_survives_classifier_merge(self) -> None:
        ctx = {"meaning_profile_lock": "standard", "turn_meaning": {"referent": REFERENT_AGENT}}
        prof = apply_meaning_profile_lock("quick_explain", ctx)
        self.assertEqual(prof, "standard")

    def test_batch_profile_overrides_meaning_lock(self) -> None:
        ctx = {"meaning_profile_lock": "standard", "brain_force_batch_profile": True}
        prof = apply_meaning_profile_lock("batch", ctx)
        self.assertEqual(prof, "batch")


if __name__ == "__main__":
    unittest.main()
