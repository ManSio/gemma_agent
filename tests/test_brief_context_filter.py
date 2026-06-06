import unittest

from core.brain.brief_context_filter import filter_recent_after_brief_trap


class BriefContextFilterTests(unittest.TestCase):
    def test_strips_brief_trap_before_explain(self):
        hist = [
            {"role": "user", "text": "скажи только: ок"},
            {"role": "assistant", "text": "396"},
        ]
        out = filter_recent_after_brief_trap(hist, "Почему земля круглая")
        self.assertEqual(out, [])

    def test_keeps_normal_history(self):
        hist = [
            {"role": "user", "text": "что такое гравитация"},
            {"role": "assistant", "text": "Сила притяжения масс."},
        ]
        out = filter_recent_after_brief_trap(hist, "Почему земля круглая")
        self.assertEqual(len(out), 2)


if __name__ == "__main__":
    unittest.main()
