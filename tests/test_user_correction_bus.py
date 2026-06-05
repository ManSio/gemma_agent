"""Петля 👎 / «не так» → урок + pending hint."""
from __future__ import annotations

import unittest

from core.user_correction_bus import (
    format_learning_ack_message,
    lesson_trigger_from_user_text,
    negative_rating_lesson_instruction,
)


class TestUserCorrectionBus(unittest.TestCase):
    def test_lesson_trigger_translation_regex(self):
        trig, rx = lesson_trigger_from_user_text("переведи на английский hello world")
        self.assertTrue(rx)
        self.assertIn("перевед", trig)

    def test_lesson_trigger_short_chunk(self):
        trig, rx = lesson_trigger_from_user_text("Почему земля круглая и как это доказали")
        self.assertFalse(rx)
        self.assertGreaterEqual(len(trig), 12)

    def test_negative_instruction_with_correction(self):
        inst = negative_rating_lesson_instruction(
            user_text="x",
            intent="general",
            module="chat-orchestrator",
            correction_text="не калькулятор, реши уравнение",
        )
        self.assertIn("уравнен", inst.lower())

    def test_learning_ack_message(self):
        msg = format_learning_ack_message(
            ["pending_correction", "ephemeral_lesson"],
            correction_text="не повторяй прошлый ответ",
        )
        self.assertIn("6", msg)
        self.assertIn("правку", msg.lower())
        self.assertIn("правило", msg.lower())


if __name__ == "__main__":
    unittest.main()
