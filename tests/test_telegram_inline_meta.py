import unittest

from aiogram.types import InlineKeyboardMarkup

from core.telegram_inline_meta import META_KEY, inline_markup_from_meta


class TelegramInlineMetaTests(unittest.TestCase):
    def test_builds_markup(self):
        meta = {
            META_KEY: [
                [{"text": "A", "callback_data": "pgen:t:foo"}],
            ]
        }
        kb = inline_markup_from_meta(meta)
        self.assertIsInstance(kb, InlineKeyboardMarkup)
        self.assertEqual(len(kb.inline_keyboard), 1)
        self.assertEqual(kb.inline_keyboard[0][0].text, "A")

    def test_empty_meta(self):
        self.assertIsNone(inline_markup_from_meta({}))
        self.assertIsNone(inline_markup_from_meta(None))


if __name__ == "__main__":
    unittest.main()
