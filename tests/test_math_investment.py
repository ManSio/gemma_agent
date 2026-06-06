import unittest

from core.math_investment import (
    text_looks_like_investment_annuity,
    try_solve_investment_annuity,
)
from core.math_linear import try_solve_linear_equation


TASK = """
Задача: Инвестиции с пополнением и налогом

Вы решили инвестировать 10 000 BYN. Каждый месяц вы добавляете ещё 500 BYN.
Доходность составляет 12% годовых (сложный процент, капитализация ежемесячная).
Через 3 года вы закрываете вклад и платите налог 13% с полученной прибыли.

Рассчитать ежемесячную ставку: 12% / 12 = 1% = 0.01.
Всего 36 пополнений (3 года × 12).
"""


class MathInvestmentTests(unittest.TestCase):
    def test_detects_investment_task(self):
        self.assertTrue(text_looks_like_investment_annuity(TASK))

    def test_linear_does_not_hijack_12_div_12(self):
        out = try_solve_linear_equation(TASK)
        self.assertIsNone(out)

    def test_investment_solver_numbers(self):
        out = try_solve_investment_annuity(TASK)
        self.assertIsNotNone(out)
        compact = out.replace(" ", "").replace("\u00a0", "")
        self.assertIn("36061", compact)
        self.assertIn("7013", compact)
        self.assertIn("7.7%", compact)


if __name__ == "__main__":
    unittest.main()
