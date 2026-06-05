import unittest

from core.dialogue_feedback_signals import (
    build_user_remark_hint,
    merge_recent_remarks_into_routing_prefs,
    user_feedback_likely,
)


class DialogueFeedbackSignalsTests(unittest.TestCase):
    def test_detects_ru(self):
        self.assertTrue(user_feedback_likely("Это не то, я имел в виду другое"))
        self.assertTrue(user_feedback_likely("Перечитай внимательно"))
        self.assertTrue(user_feedback_likely("Ты сьехал с темы, вернись к вопросу"))
        self.assertTrue(user_feedback_likely("Ответ неточный, проверь факты"))

    def test_negative(self):
        self.assertFalse(user_feedback_likely("Привет, как дела?"))
        self.assertFalse(user_feedback_likely("ок"))

    def test_merge_remarks(self):
        rp: dict = {}
        merge_recent_remarks_into_routing_prefs(rp, "не так понял задачу")
        self.assertTrue(rp.get("recent_user_remarks"))
        merge_recent_remarks_into_routing_prefs(rp, "не так понял задачу")
        self.assertEqual(len(rp.get("recent_user_remarks") or []), 1)

    def test_hint_builds(self):
        h = build_user_remark_hint(
            user_text="Исправь ответ, ты ошибся",
            routing_prefs={"recent_user_remarks": ["раньше: убери калькулятор"]},
        )
        self.assertIn("Обратная связь", h)
        self.assertIn("раньше", h)


if __name__ == "__main__":
    unittest.main()
