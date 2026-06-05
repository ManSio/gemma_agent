import unittest

from core.brain.content_hints import intimate_health_education_hint


class TestContentHints(unittest.TestCase):
    def test_postpartum_intimate_hint(self):
        h = intimate_health_education_hint(
            "Так как возбудить подробно девушку которая рожала"
        )
        self.assertIn("послеродов", h.lower())

    def test_no_hint_for_unrelated(self):
        self.assertEqual(intimate_health_education_hint("Какие новости в мире"), "")


if __name__ == "__main__":
    unittest.main()
