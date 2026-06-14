"""Tests for template-aware anti-echo guard."""
from __future__ import annotations

import unittest

from core.anti_echo_guard import (
    detect_template_echo_issues,
    recover_template_echo_reply,
    user_question_bucket,
)


class TestAntiEchoGuard(unittest.TestCase):
    def test_weather_on_identity_question(self) -> None:
        wx = (
            "Погода (wttr.in) Startsevichi: 🌡 +12°C, ветер: 5 km/h, влажность 80%"
        )
        issues = detect_template_echo_issues("как меня зовут?", wx, wx)
        self.assertIn("template_echo_weather", issues)
        rep = recover_template_echo_reply("как меня зовут?", issues)
        self.assertIn("погод", rep.lower())

    def test_weather_on_weather_ok(self) -> None:
        wx = "Погода (wttr.in) Minsk: 🌡 +5°C"
        issues = detect_template_echo_issues("какая погода в минске?", wx)
        self.assertEqual(issues, [])

    def test_intentional_repeat_allowed(self) -> None:
        wx = "Погода (wttr.in) Minsk: 🌡 +5°C"
        issues = detect_template_echo_issues("повтори погоду", wx, wx)
        self.assertEqual(issues, [])

    def test_user_bucket(self) -> None:
        self.assertEqual(user_question_bucket("какой сегодня день"), "day")
        self.assertEqual(user_question_bucket("как меня зовут"), "identity")


if __name__ == "__main__":
    unittest.main()
