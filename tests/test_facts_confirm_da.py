"""«да» после Запомнить страну — commit, не news search."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from core.brain.text_helpers import (
    affirmative_overrides_fact_confirmation,
    resolve_affirmative_search_query,
)
from core.user_facts import (
    UserFactsManager,
    has_pending_facts_confirmation,
    try_facts_shortcut_payload,
)


class FactsConfirmDaTests(unittest.TestCase):
    def test_has_pending_from_behavior_store(self) -> None:
        rec = {"pending_facts_confirmation": {"country": {"value": "BY"}}}
        self.assertTrue(has_pending_facts_confirmation(rec))

    def test_affirmative_blocked_with_store_pending(self) -> None:
        recent = [
            {"role": "user", "text": "какие новости"},
            {"role": "assistant", "text": "Главные мировые новости…"},
            {"role": "user", "text": "моя страна Беларусь"},
            {"role": "assistant", "text": "Запомнить страну? Ответь «да» или «нет»."},
        ]
        rec = {
            "pending_facts_confirmation": {
                "country": {"value": "Беларусь", "field": "country", "valid": True},
            },
            "user_facts": {},
            "user_facts_meta": {},
            "recent_messages": recent,
        }
        self.assertIsNone(resolve_affirmative_search_query("да", recent, rec))
        self.assertFalse(
            affirmative_overrides_fact_confirmation("да", recent_dialogue=recent, persisted=rec)
        )

    def test_process_turn_da_commits_country(self) -> None:
        store = MagicMock()
        rec = {
            "user_facts": {},
            "user_facts_meta": {},
            "pending_facts_confirmation": {
                "country": {
                    "field": "country",
                    "value": "Беларусь",
                    "valid": True,
                    "confidence": 0.9,
                },
            },
            "pending_facts_overwrite": {},
            "recent_messages": [],
        }
        store.load.return_value = dict(rec)
        mgr = UserFactsManager(
            behavior_store=store, digital_twin=None, mem0_memory=None
        )
        mgr.commit_validated = MagicMock()
        flow = mgr.process_turn("test_user_facts_da", None, "да")
        mgr.commit_validated.assert_called_once()
        self.assertTrue(flow.get("committed_facts_this_turn"))
        ack = try_facts_shortcut_payload(
            "да",
            flow,
            recent_dialogue=rec.get("recent_messages"),
            persisted=rec,
        )
        self.assertEqual(ack, "Запомнил.")

    def test_committed_da_ack_not_blocked_by_stale_news_dialogue(self) -> None:
        recent = [
            {"role": "user", "text": "новости"},
            {"role": "assistant", "text": "могу перепроверить поиск по этой теме"},
            {"role": "user", "text": "моя страна Беларусь"},
            {"role": "assistant", "text": "Запомнить страну? Ответь «да» или «нет»."},
        ]
        flow = {
            "facts": {"country": "Беларусь"},
            "pending_confirmation": {},
            "committed_facts_this_turn": True,
        }
        rec = {"user_facts": {"country": "Беларусь"}, "recent_messages": recent}
        self.assertEqual(
            try_facts_shortcut_payload("да", flow, recent_dialogue=recent, persisted=rec),
            "Запомнил.",
        )


if __name__ == "__main__":
    unittest.main()
