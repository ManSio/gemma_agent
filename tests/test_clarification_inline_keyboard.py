import unittest

from core.clarification_inline_keyboard import (
    fact_auto_ask_keyboard_rows,
    fact_confirmation_keyboard_rows,
    merge_telegram_inline_rows,
)
from core.telegram_inline_meta import META_KEY


class TestClarificationInlineKeyboard(unittest.TestCase):
    def test_confirmation_yes_no(self):
        rows = fact_confirmation_keyboard_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0]["callback_data"], "factcfm:y")
        self.assertEqual(rows[0][1]["callback_data"], "factcfm:n")

    def test_location_quick_replies(self):
        rows = fact_auto_ask_keyboard_rows(["location"])
        self.assertTrue(any("factask:tx:" in c["callback_data"] for r in rows for c in r))

    def test_merge_context(self):
        ctx: dict = {}
        merge_telegram_inline_rows(ctx, fact_confirmation_keyboard_rows())
        self.assertIn(META_KEY, ctx)
        merge_telegram_inline_rows(ctx, fact_auto_ask_keyboard_rows(["currency"]))
        self.assertEqual(len(ctx[META_KEY]), 3)


if __name__ == "__main__":
    unittest.main()
