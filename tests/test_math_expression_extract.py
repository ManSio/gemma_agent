import unittest

from core.math_expression_extract import extract_arithmetic_expression, extract_percent_of_expression


class MathExpressionExtractTests(unittest.TestCase):
    def test_poschitay_compact(self):
        self.assertEqual(extract_arithmetic_expression("Посчитай 23+45*45"), "23+45*45")

    def test_slash_command_returns_none(self):
        self.assertIsNone(extract_arithmetic_expression("/calc 2+2"))

    def test_prose_without_formula_returns_none(self):
        self.assertIsNone(
            extract_arithmetic_expression(
                "Часть A. Внимательно посчитай, сколько раз встречается маркер."
            )
        )

    def test_percent_of(self):
        self.assertEqual(extract_percent_of_expression("посчитай 15% от 2500"), "(2500.0)*(15.0/100)")
        expr = extract_arithmetic_expression("Посчитай 15% от 2500")
        self.assertEqual(expr, "(2500.0)*(15.0/100)")

    def test_equation_not_rhs_only(self):
        self.assertIsNone(extract_arithmetic_expression("решить уравнение: 2x + 5 = 15"))


if __name__ == "__main__":
    unittest.main()
