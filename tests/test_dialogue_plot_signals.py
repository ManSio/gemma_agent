import unittest

from core.dialogue_plot_signals import plot_twist_likely


class PlotTwistSignalsTests(unittest.TestCase):
    def test_divorce_ru(self):
        self.assertTrue(plot_twist_likely("Ирина быстро развелась и ушла"))
        self.assertTrue(plot_twist_likely("мы расстались"))

    def test_negative(self):
        self.assertFalse(plot_twist_likely("Как приготовить борщ?"))
        self.assertFalse(plot_twist_likely(""))

    def test_reset_phrase(self):
        self.assertTrue(plot_twist_likely("Забудь про прошлый сценарий, новая история"))


if __name__ == "__main__":
    unittest.main()
