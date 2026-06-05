import unittest

from core.brain.response_finalize import finalize_user_reply
from core.brain.translation_path import (
    is_translation_turn,
    parse_translation_request,
    parse_translation_requests,
    translation_external_hint,
)
from core.brain.translation_reply import sanitize_translation_reply


class TranslationPathTests(unittest.TestCase):
    def test_detect_translate(self):
        self.assertTrue(is_translation_turn("Переведи на английский: 'Привет'"))
        self.assertTrue(is_translation_turn("по-французски: bonjour"))
        self.assertTrue(is_translation_turn("German: Guten Tag"))
        self.assertFalse(is_translation_turn("Привет, как дела"))

    def test_parse_quoted(self):
        tgt, frag = parse_translation_request("Переведи на английский: 'Привет, как дела'")
        self.assertEqual(tgt, "en")
        self.assertEqual(frag, "Привет, как дела")

    def test_parse_po_french(self):
        tgt, frag = parse_translation_request('скажи по-французски "здравствуйте"')
        self.assertEqual(tgt, "fr")
        self.assertEqual(frag, "здравствуйте")

    def test_parse_multiline(self):
        reqs = parse_translation_requests(
            'переведи на английский: "спокойной ночи"\n· скажи по-французски "здравствуйте"'
        )
        self.assertEqual(len(reqs), 2)
        self.assertEqual(reqs[0][0], "en")
        self.assertEqual(reqs[0][1], "спокойной ночи")
        self.assertEqual(reqs[1][0], "fr")
        self.assertEqual(reqs[1][1], "здравствуйте")

    def test_external_hint_no_tool_call(self):
        h = translation_external_hint("Переведи на английский: 'test'")
        self.assertIn("только", h.lower())
        self.assertIn("TOOL_CALL", h)

    def test_finalize_strips_meta_tool_note(self):
        bad = (
            "Примечание: для перевода слов, фраз, предложений не вызывай никаких TOOL_CALL — "
            "переводи самостоятельно."
        )
        out = finalize_user_reply(bad, user_text="Переведи на английский: hi")
        self.assertEqual(out, "")

    def test_sanitize_picks_english_line(self):
        garbage = (
            "Available tools (названия в русской локали):\n"
            "Admin, ArithmeticTool.\n"
            "Системное сообщение: не вызывай TOOL_CALL.\n"
            "Hello, how are you?"
        )
        out = sanitize_translation_reply(garbage, target_lang="en", source_fragment="Привет, как дела")
        self.assertEqual(out, "Hello, how are you?")
