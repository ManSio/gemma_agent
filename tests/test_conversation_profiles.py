import unittest

from core.conversation_profiles import (
    DEFAULT_STYLE,
    normalize_conversation_style,
    system_addon_for_conversation_style,
)


class ConversationProfilesTests(unittest.TestCase):
    def test_normalize_unknown(self):
        self.assertEqual(normalize_conversation_style("nope"), DEFAULT_STYLE)

    def test_normalize_easy(self):
        self.assertEqual(normalize_conversation_style("easy"), "easy")

    def test_addon_non_empty(self):
        self.assertIn("простой", system_addon_for_conversation_style("easy").lower())
        self.assertIn("эксперт", system_addon_for_conversation_style("expert").lower())


if __name__ == "__main__":
    unittest.main()
