import unittest

from core.calc_slash import is_calc_slash_payload, strip_calc_command


class CalcSlashTests(unittest.TestCase):
    def test_strip_plain_calc(self):
        self.assertEqual(strip_calc_command("/calc 2+2"), "2+2")
        self.assertEqual(strip_calc_command("/calc (12+3)*4"), "(12+3)*4")

    def test_strip_calc_at_bot(self):
        self.assertEqual(strip_calc_command("/calc@MyGemmaBot 2+2"), "2+2")
        self.assertEqual(strip_calc_command("/calc@bot (1+2)*3"), "(1+2)*3")

    def test_not_calculator_command(self):
        self.assertIsNone(strip_calc_command("/calculator 2+2"))
        self.assertIsNone(strip_calc_command("/calendar"))

    def test_is_calc_slash_payload(self):
        self.assertTrue(is_calc_slash_payload("/calc 1"))
        self.assertTrue(is_calc_slash_payload("/calc@x 1"))
        self.assertFalse(is_calc_slash_payload("/calculator 1"))


if __name__ == "__main__":
    unittest.main()
