"""Валидаторы agent test runner."""

import unittest

from core.agent_test_validators import validate_reply


class AgentTestValidatorsTests(unittest.TestCase):
    def test_short_ok_passes(self):
        case = {"validators": ["no_fallback", "no_leak", "expect_regex"], "expect_regex": r"^ок\s*$"}
        self.assertEqual(validate_reply("ок", "скажи только: ок", case), [])

    def test_fallback_fails(self):
        case = {"validators": ["no_fallback"]}
        errs = validate_reply("Не удалось сформировать нормальный ответ.", "x", case)
        self.assertTrue(any("fallback" in e for e in errs))

    def test_xml_leak_fails(self):
        case = {"validators": ["no_leak"]}
        txt = 'priority="override_internal"><rule name="x">'
        errs = validate_reply(txt, "x", case)
        self.assertTrue(errs)


if __name__ == "__main__":
    unittest.main()
