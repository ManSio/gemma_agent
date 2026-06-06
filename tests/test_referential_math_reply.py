import unittest

from core.referential_math_reply import (
    extract_anchor_number,
    try_referential_math_reply,
)


class ReferentialMathReplyTests(unittest.TestCase):
    def test_extract_anchor_from_short_reply(self) -> None:
        self.assertEqual(extract_anchor_number("143"), 143.0)
        self.assertEqual(extract_anchor_number("143.0"), 143.0)

    def test_chain_add_seven(self) -> None:
        recent = [
            {"role": "user", "text": "сколько будет 11*13, ответь только числом"},
            {"role": "assistant", "text": "143"},
        ]
        out = try_referential_math_reply(
            "к тому числу прибавь 7, снова только число",
            recent_dialogue=recent,
        )
        self.assertEqual(out, "150")

    def test_skips_clarify_with_digit_in_question(self) -> None:
        recent = [
            {"role": "assistant", "text": "Результат: 143.0"},
            {"role": "assistant", "text": "Пожалуйста, укажите число, к которому нужно прибавить 7."},
        ]
        out = try_referential_math_reply("к тому числу прибавь 7", recent_dialogue=recent)
        self.assertEqual(out, "150")

    def test_skips_clarify_uses_prior_numeric_assistant(self) -> None:
        recent = [
            {"role": "user", "text": "11*13"},
            {"role": "assistant", "text": "Результат: 143.0"},
            {"role": "user", "text": "к тому числу прибавь 7"},
            {"role": "assistant", "text": "Пожалуйста, укажите число"},
        ]
        out = try_referential_math_reply("к тому числу прибавь 7, только число", recent_dialogue=recent)
        self.assertEqual(out, "150")

    def test_no_anchor_returns_none(self) -> None:
        out = try_referential_math_reply(
            "к тому числу прибавь 7",
            recent_dialogue=[{"role": "assistant", "text": "не знаю"}],
        )
        self.assertIsNone(out)
