"""Tests for hard context limit enforcement in core/context_collapse.py."""

import unittest

from core.context_collapse import enforce_context_limit


class ContextHardLimitTests(unittest.TestCase):
    def test_no_op_when_under_limit(self):
        """Under budget — parts unchanged."""
        parts = {
            "system_prompt_for_llm": "sys",
            "user_text": "hi",
            "memory_facts": "small",
        }
        out, meta = enforce_context_limit(parts, max_tokens=5000)
        self.assertIs(out, parts)
        self.assertFalse(meta.get("enforced"))
        self.assertEqual(meta.get("tokens_after"), meta.get("tokens_before"))

    def test_prunes_low_priority_first(self):
        """Overflow — archive/memory pruned, system and user_text intact."""
        parts = {
            "system_prompt_for_llm": "S" * 2000,
            "user_text": "question",
            "message_archive": "A" * 120000,
            "memory_facts": "M" * 80000,
        }
        sys_before = parts["system_prompt_for_llm"]
        user_before = parts["user_text"]
        _, meta = enforce_context_limit(parts, max_tokens=5000)
        self.assertTrue(meta.get("enforced"))
        self.assertLessEqual(meta.get("tokens_after", 0), 5000)
        self.assertEqual(parts["system_prompt_for_llm"], sys_before)
        self.assertEqual(parts["user_text"], user_before)
        self.assertIn("message_archive", meta.get("pruned_keys", []))

    def test_recent_dialogue_list_halved(self):
        """List dialogue is trimmed while protected keys stay."""
        rows = [{"role": "user", "text": "x" * 400} for _ in range(20)]
        parts = {
            "system_prompt_for_llm": "sys",
            "user_text": "q",
            "recent_dialogue": rows,
        }
        _, meta = enforce_context_limit(parts, max_tokens=800)
        self.assertTrue(meta.get("enforced"))
        self.assertLess(len(parts["recent_dialogue"]), len(rows))


if __name__ == "__main__":
    unittest.main()
