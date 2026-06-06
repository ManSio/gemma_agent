import unittest

from core.user_facing_plain import (
    format_books_search_plain,
    format_group_behavior_plain,
    format_psych_core_plain,
    format_quiz_plain,
    format_twin_plain,
)


class UserFacingPlainTests(unittest.TestCase):
    def test_psych_core_plain_no_braces_dump(self):
        s = format_psych_core_plain(
            {
                "last_analysis": {
                    "sentiment": "neutral",
                    "stress_signals": False,
                    "keywords": [],
                    "analyzed_at": "t",
                    "message_length": 1,
                },
                "stress_streak": 0,
                "updated_at": "u",
            }
        )
        self.assertIn("нейтральная", s)
        self.assertNotIn("{", s)

    def test_twin_plain_location(self):
        s = format_twin_plain({"user_id": "1", "location": {"city": None, "country": "BY"}})
        self.assertIn("Локация", s)
        self.assertIn("BY", s)

    def test_books_search_plain(self):
        s = format_books_search_plain([{"chunk_id": 0, "content": "hello", "match_type": "x"}])
        self.assertIn("hello", s)
        self.assertIn("Фрагмент", s)

    def test_group_behavior_plain(self):
        s = format_group_behavior_plain(
            {
                "group_id": "g1",
                "group_type": "normal_group",
                "message": "hi",
                "timestamp": "t",
                "behavior_analysis": {
                    "social_cues": True,
                    "process_indicators": False,
                    "engagement_level": "high",
                    "conversation_flow": "natural",
                },
                "should_intervene": False,
                "response_template": "ok",
            }
        )
        self.assertIn("g1", s)
        self.assertIn("Вмешаться", s)

    def test_quiz_plain(self):
        s = format_quiz_plain(
            {
                "subject": "math",
                "questions": [{"question": "Q?", "options": ["a", "b"], "correct": "a"}],
            }
        )
        self.assertIn("Q?", s)
        self.assertIn("a", s)


if __name__ == "__main__":
    unittest.main()
