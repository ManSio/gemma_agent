"""Outbound thread guard + turn_meaning audit emit + intent from meaning."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from core.outbound_thread_guard import (
    detect_thread_followup_issues,
    recover_thread_followup_reply,
)
from core.turn_decision_spine import intent_hint_from_turn_meaning
from core.turn_meaning import (
    REFERENT_AGENT,
    REFERENT_THREAD,
    apply_turn_meaning_to_context,
    resolve_turn_meaning_structural,
)
from core.turn_reconcile import turn_meaning_audit_for_emit


class OutboundThreadGuardTests(unittest.TestCase):
    def _earth_ctx(self) -> dict:
        long_a = (
            "Земля почти сферическая: гравитация сжимает массу планеты в шар "
            "на протяжении миллиардов лет формирования."
        )
        return {
            "discourse_resolution": {
                "last_user_q": "Почему земля круглая и как это доказали?",
            },
            "recent_dialogue": [
                {"role": "user", "text": "Почему земля круглая и как это доказали?"},
                {"role": "assistant", "text": long_a},
            ],
            "session_task": {"last_outcome": "ok"},
            "turn_meaning": {
                "referent": REFERENT_THREAD,
                "thread_action": "stay",
            },
        }

    def test_agent_meta_on_followup_detected(self) -> None:
        meta = self._earth_ctx()
        rep = (
            "У меня ограничения LLM: я не могу проверить баланс OpenRouter. "
            "Мои возможности зависят от архитектуры бота gemma_bot."
        )
        issues = detect_thread_followup_issues("почему так произошло?", rep, meta)
        self.assertIn("thread_followup_agent_meta", issues)

    def test_recover_stays_on_anchor(self) -> None:
        meta = self._earth_ctx()
        recovered = recover_thread_followup_reply(
            "почему так произошло?",
            "meta bad",
            ["thread_followup_agent_meta"],
            output_meta=meta,
        )
        self.assertIn("земля", recovered.lower())
        self.assertNotIn("openrouter", recovered.lower())

    def test_physics_reply_not_blocked(self) -> None:
        meta = self._earth_ctx()
        rep = (
            "Земля сформировалась почти шаром из-за гравитации: масса притягивает "
            "материал к центру, поэтому планета сжимается в сферу."
        )
        issues = detect_thread_followup_issues("почему так произошло?", rep, meta)
        self.assertEqual(issues, [])


class TurnMeaningAuditEmitTests(unittest.TestCase):
    def test_audit_from_turn_meaning_dict(self) -> None:
        ctx = {
            "turn_meaning": {
                "referent": REFERENT_AGENT,
                "thread_action": "branch",
                "speech_act": "question",
                "source": "structural",
            }
        }
        audit = turn_meaning_audit_for_emit(ctx)
        self.assertIsNotNone(audit)
        self.assertEqual(audit.get("referent"), REFERENT_AGENT)

    def test_audit_fallback_turn_state(self) -> None:
        ctx = {
            "turn_state_audit": {
                "referent": REFERENT_THREAD,
                "thread_action": "stay",
                "meaning_source": "structural",
            }
        }
        audit = turn_meaning_audit_for_emit(ctx)
        self.assertIsNotNone(audit)
        self.assertEqual(audit.get("thread_action"), "stay")


class IntentFromTurnMeaningTests(unittest.TestCase):
    def test_agent_referent_explain(self) -> None:
        ctx = apply_turn_meaning_to_context(
            {},
            resolve_turn_meaning_structural("какие проблемы у тебя сейчас есть?", {}),
        )
        self.assertEqual(intent_hint_from_turn_meaning(ctx), "explain")

    def test_correction_inherits_intent(self) -> None:
        ctx = {
            "turn_meaning": {"thread_action": "correct", "referent": "world"},
            "discourse_resolution": {"inherit_intent": "explain"},
        }
        self.assertEqual(intent_hint_from_turn_meaning(ctx), "explain")


if __name__ == "__main__":
    unittest.main()
