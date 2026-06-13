"""Детерминированный recall user_facts: «как меня зовут» / «кто я»."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from core.intent_heuristics import detect_pre_llm_shortcut
from core.pre_llm_plan import PRE_LLM_DIRECT_VARIANTS, try_pre_llm_direct_plan
from core.user_facts import (
    build_user_facts_identity_reply,
    plain_text_requests_user_facts_identity,
)


class UserFactsIdentityRecallTests(unittest.TestCase):
    def test_marker_identity_question(self) -> None:
        q = "как меня зовут? и кто я?"
        self.assertTrue(plain_text_requests_user_facts_identity(q))
        self.assertEqual(detect_pre_llm_shortcut(q), "user_facts_identity")

    def test_not_bot_name_question(self) -> None:
        self.assertFalse(plain_text_requests_user_facts_identity("как тебя зовут?"))

    def test_build_reply_with_name(self) -> None:
        out = build_user_facts_identity_reply(
            {
                "name": "Михаил",
                "city": "аг. Михановичи",
                "pet_cat": "Мурза",
            }
        )
        self.assertIn("Михаил", out)
        self.assertIn("Мурза", out)

    def test_build_reply_empty(self) -> None:
        out = build_user_facts_identity_reply({})
        self.assertIn("пусто", out.lower())

    def test_pre_llm_plan_direct(self) -> None:
        facts = {"name": "Михаил", "country": "Беларусь"}
        with patch(
            "core.user_facts.brain_user_facts_from_store",
            return_value=(facts, {}),
        ):
            got = try_pre_llm_direct_plan(
                user_id="test-user-1",
                group_id=None,
                text="как меня зовут?",
                persisted={"user_facts": {}},
                input_meta={},
            )
        self.assertIsNotNone(got)
        self.assertEqual(got[0], "user_facts_identity_nl")
        self.assertIn("Михаил", got[1])

    def test_pre_llm_variant_whitelisted(self) -> None:
        self.assertIn("user_facts_identity_nl", PRE_LLM_DIRECT_VARIANTS)

    def test_disabled_by_env(self) -> None:
        with patch.dict(os.environ, {"USER_FACTS_IDENTITY_RECALL_ENABLED": "false"}, clear=False):
            self.assertFalse(plain_text_requests_user_facts_identity("как меня зовут?"))


if __name__ == "__main__":
    unittest.main()
