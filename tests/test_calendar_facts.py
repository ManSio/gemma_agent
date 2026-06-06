import unittest

from core.calendar_facts import build_calendar_date_hint_for_llm


class CalendarFactsTests(unittest.TestCase):
    def test_stress_template_date_ru_dd_mm_and_us(self):
        q = "02.05.2026 — это какая дата по ISO и какой день недели? (ожидаемо: явная трактовка ДД.ММ или ММ.ДД)"
        h = build_calendar_date_hint_for_llm(q)
        self.assertIn("2026-05-02", h)
        self.assertIn("суббота", h)
        self.assertIn("2026-02-05", h)
        self.assertIn("четверг", h)

    def test_unambiguous_day_13(self):
        q = "13.05.2026 день недели по iso?"
        h = build_calendar_date_hint_for_llm(q)
        self.assertIn("2026-05-13", h)
        self.assertNotIn("Если ММ.ДД", h)

    def test_no_trigger_without_keywords(self):
        q = "Встреча 02.05.2026 в десять"
        self.assertEqual(build_calendar_date_hint_for_llm(q), "")


if __name__ == "__main__":
    unittest.main()
