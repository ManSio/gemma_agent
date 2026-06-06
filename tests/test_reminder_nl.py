import time
import unittest

from core.reminder_dispatch import parse_due_ts
from core.reminder_nl import (
    extract_reminder_label,
    looks_like_reminder_request,
    try_schedule_natural_reminder,
)


class ReminderNLTests(unittest.TestCase):
    def test_poschitay_sleep_phrase_label(self):
        label = extract_reminder_label("Напомни мне сегодня в 22:50 чтобы я пошла спать")
        self.assertIn("спат", label.lower())

    def test_looks_like_reminder_with_time(self):
        self.assertTrue(looks_like_reminder_request("напомни сегодня в 22:50 спать"))

    def test_looks_like_false_without_time(self):
        self.assertFalse(looks_like_reminder_request("напомни как дела"))

    def test_channel_post_with_napominanie_noun_not_reminder(self):
        post = (
            "Для тех, кто строит продукты на агентах, это конкретное напоминание. "
            "Главный вопрос про агента в продакшене не «как ускорить», а «где границы». "
            "15 дней без присмотра — это много.\n"
            "🔗 Channel 4 News про эксперимент"
        )
        self.assertFalse(looks_like_reminder_request(post))
        res = try_schedule_natural_reminder("u_ai_post", post)
        self.assertIsNone(res)

    def test_schedule_natural_reminder(self):
        import os
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        os.environ["GEMMA_PROJECT_ROOT"] = str(root)
        res = try_schedule_natural_reminder("u_test_nl", "Напомни через 5 минут выпить воду")
        self.assertIsNotNone(res)
        self.assertTrue(res.get("ok"))
        self.assertIn("напомню", res.get("reply", "").lower())

    def test_parse_tomorrow_time(self):
        due = parse_due_ts("напомни завтра в 09:30 позвонить", user_id="")
        self.assertIsNotNone(due)
        self.assertGreater(due, int(time.time()))

    def test_parse_relative_hours(self):
        due = parse_due_ts("напомни через 2 часа обед", user_id="")
        self.assertIsNotNone(due)
        self.assertGreater(due, int(time.time()) + 3500)

    def test_parse_vecherom_evening(self):
        due = parse_due_ts("напомни купить молоко вечером", user_id="")
        self.assertIsNotNone(due)
        self.assertGreater(due, int(time.time()))

    def test_schedule_vecherom_milk(self):
        import os
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        os.environ["GEMMA_PROJECT_ROOT"] = str(root)
        res = try_schedule_natural_reminder("u_vecher", "Напомни купить молоко вечером")
        self.assertIsNotNone(res)
        self.assertTrue(res.get("ok"))
        self.assertIn("молок", res.get("reply", "").lower())


if __name__ == "__main__":
    unittest.main()
