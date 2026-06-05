"""ACC-1 dialog chains: corpus reg_chain_acc* must stay in build_test_corpus."""
from __future__ import annotations

import unittest

from scripts.build_test_corpus import _acc_dialog_chain_cases


class TestRegChainAccCorpus(unittest.TestCase):
    def test_acc_chain_cases_present(self):
        cases = _acc_dialog_chain_cases()
        ids = {c["id"] for c in cases}
        expected = {
            "reg_chain_acc1_pivot_finance_physics",
            "reg_chain_acc_math_followup",
            "reg_chain_acc_chitchat_then_math",
        }
        self.assertEqual(ids, expected)

    def test_acc_chain_schema(self):
        for case in _acc_dialog_chain_cases():
            turns = case.get("dialog_turns") or []
            self.assertGreaterEqual(len(turns), 2, case.get("id"))
            tags = case.get("tags") or []
            self.assertIn("acc_chain", tags, case.get("id"))
            self.assertIn("no_fallback", case.get("validators") or [], case.get("id"))
            self.assertTrue(str(case.get("expect_regex") or "").strip(), case.get("id"))


if __name__ == "__main__":
    unittest.main()
