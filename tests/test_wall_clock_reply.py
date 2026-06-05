import unittest

from core.scenario_engine import _fix_glued_day_adverb
from core.timezone_inference import format_wall_clock_user_reply, looks_like_wall_clock_question


class WallClockReplyTests(unittest.TestCase):
    def test_looks_like_wall_clock(self):
        self.assertTrue(looks_like_wall_clock_question("Который час"))
        self.assertTrue(looks_like_wall_clock_question("Сколько сейчас времени"))

    def test_format_minsk_tz(self):
        out = format_wall_clock_user_reply(effective_tz="Europe/Minsk")
        self.assertIn(":", out)
        self.assertIn("минск", out.lower())

    def test_glued_weather_fix(self):
        self.assertIn(
            "Минске сегодня",
            _fix_glued_day_adverb("В Минскесегодня облачно"),
        )


if __name__ == "__main__":
    unittest.main()
