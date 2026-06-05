"""Тесты classify_turn_outcome и recheck deterministic."""
from __future__ import annotations

import unittest

from core.dialogue_recheck_reply import try_recheck_deterministic_reply, try_trivia_recheck_answer
from core.experience_memory import classify_turn_outcome


class _Out:
    def __init__(self, payload: str, meta: dict):
        self.type = "text"
        self.payload = payload
        self.meta = meta


class ClassifyTurnOutcomeTests(unittest.TestCase):
    def test_clarify_question_without_meta(self):
        payload = "Что именно вы имеете в виду — A или B?"
        outs = [_Out(payload, {})]
        self.assertEqual(classify_turn_outcome(outs, user_text="непонятно"), "clarify")

    def test_clarify_via_meta_confirmation(self):
        outs = [_Out("A или B?", {"confirmation": True})]
        self.assertEqual(classify_turn_outcome(outs), "clarify")

    def test_nl_reminder_is_ok_not_fallback(self):
        outs = [_Out("Ок, напомню", {"module": "__fallback__", "reason": "nl_reminder"})]
        self.assertEqual(classify_turn_outcome(outs), "ok")

    def test_article_thread_followup_nl_is_ok_not_fallback(self):
        outs = [
            _Out(
                "По этой теме мало нового.",
                {"module": "__fallback__", "reason": "article_thread_followup_nl"},
            )
        ]
        self.assertEqual(classify_turn_outcome(outs), "ok")

    def test_unknown_fallback_stays_fallback(self):
        outs = [_Out("x", {"module": "__fallback__", "reason": "unknown_variant"})]
        self.assertEqual(classify_turn_outcome(outs), "fallback")


class RecheckReplyTests(unittest.TestCase):
    def test_google_letter_recheck(self):
        q = 'Сколько букв «Р» в слове Google?'
        self.assertIn("Ноль", try_trivia_recheck_answer(q) or "")

    def test_recheck_followup(self):
        rd = [
            {"role": "user", "text": "Сколько букв «Р» в слове Google?"},
            {"role": "assistant", "text": "Ноль."},
        ]
        out = try_recheck_deterministic_reply(
            "Может ты хорошо посмотришь?",
            recent_dialogue=rd,
            user_facts={},
        )
        self.assertIn("Ноль", out or "")


if __name__ == "__main__":
    unittest.main()
