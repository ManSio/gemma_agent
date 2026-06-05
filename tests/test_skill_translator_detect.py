import unittest

from modules.skills.router import detect_skill_intent


class SkillTranslatorDetectTests(unittest.TestCase):
    def test_explicit_translate(self):
        self.assertEqual(detect_skill_intent("Переведи на английский привет"), "translator")

    def test_lang_inline(self):
        self.assertEqual(detect_skill_intent("по-немецки: Hallo"), "translator")
        self.assertEqual(detect_skill_intent("French: hello"), "translator")


if __name__ == "__main__":
    unittest.main()
