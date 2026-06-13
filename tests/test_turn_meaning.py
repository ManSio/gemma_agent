"""TurnMeaning — единый verdict намерения хода."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from core.brain.discourse_resolver import ACTION_BRANCH, ACTION_CORRECT, ACTION_STAY, resolve_discourse
from core.turn_meaning import (
    ACTION_CORRECT as MEANING_CORRECT,
    REFERENT_AGENT,
    REFERENT_THREAD,
    SPEECH_CORRECTION,
    apply_turn_meaning_to_context,
    resolve_turn_meaning_structural,
    routing_hint_for_meaning,
    turn_meaning_llm_needed,
)


class TurnMeaningStructuralTests(unittest.TestCase):
    def test_prior_clarify_is_correction(self) -> None:
        ctx = {
            "session_task": {"last_outcome": "clarify"},
            "dialogue_state": {
                "last_intent": "explain",
                "last_assistant_excerpt": "Из системы: вас зовут Михаил.",
            },
            "recent_dialogue": [
                {"role": "user", "text": "почему название ии не соответствует реалиям"},
                {"role": "assistant", "text": "Из системы: вас зовут Михаил."},
                {"role": "user", "text": "я про другое"},
            ],
        }
        meaning = resolve_turn_meaning_structural("я про другое", ctx)
        self.assertEqual(meaning.thread_action, MEANING_CORRECT)
        self.assertEqual(meaning.speech_act, SPEECH_CORRECTION)
        self.assertEqual(meaning.reason, "prior_unsatisfactory")

    def test_discourse_reads_turn_meaning(self) -> None:
        ctx = apply_turn_meaning_to_context(
            {"recent_dialogue": []},
            resolve_turn_meaning_structural(
                "я про другое",
                {
                    "session_task": {"last_outcome": "clarify"},
                    "dialogue_state": {
                        "last_assistant_excerpt": "факты из профиля",
                        "last_intent": "explain",
                    },
                    "recent_dialogue": [
                        {"role": "user", "text": "почему название ии"},
                        {"role": "assistant", "text": "факты из профиля"},
                    ],
                },
            ),
        )
        res = resolve_discourse("я про другое", ctx)
        self.assertEqual(res.action, ACTION_CORRECT)

    def test_agent_referent_hint(self) -> None:
        hint = routing_hint_for_meaning(
            {"referent": REFERENT_AGENT, "thread_action": ACTION_BRANCH}
        )
        self.assertIn("ассистент", hint.lower())
        self.assertIn("не уходи", hint.lower())

    def test_llm_needed_on_structural_stay(self) -> None:
        from core.turn_meaning import TurnMeaning

        meaning = TurnMeaning(
            thread_action=ACTION_STAY,
            inherit_thread=True,
            source="structural",
        )
        self.assertTrue(turn_meaning_llm_needed(meaning, {"recent_dialogue": [{}, {}]}))

    def test_llm_not_needed_on_correction(self) -> None:
        from core.turn_meaning import TurnMeaning

        meaning = TurnMeaning(thread_action=MEANING_CORRECT, source="structural")
        self.assertFalse(turn_meaning_llm_needed(meaning, {}))


class TurnMeaningAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_merge_llm_referent_agent(self) -> None:
        from core.turn_meaning import resolve_turn_meaning_async

        ctx = {
            "recent_dialogue": [
                {"role": "user", "text": "почему название ии"},
                {"role": "assistant", "text": "ответ про ии"},
            ],
            "dialogue_state": {"last_intent": "explain", "last_assistant_excerpt": "ответ про ии"},
        }
        judged = {
            "thread_action": ACTION_BRANCH,
            "speech_act": "question",
            "referent": REFERENT_AGENT,
            "inherit_thread": False,
            "confidence": 0.88,
            "source": "llm",
        }
        with patch(
            "core.brain.discourse_thread_judge.judge_thread_async",
            new_callable=AsyncMock,
            return_value=judged,
        ):
            meaning = await resolve_turn_meaning_async(
                "какие проблемы у тебя сейчас есть?",
                ctx,
                llm=object(),
            )
        self.assertEqual(meaning.referent, REFERENT_AGENT)
        self.assertEqual(meaning.source, "llm")
        self.assertEqual(meaning.thread_action, ACTION_BRANCH)


if __name__ == "__main__":
    unittest.main()
