import unittest

from core.behavior_store import topic_tracking_for_turn


class TopicTrackingForTurnTests(unittest.TestCase):
    def test_new_question_replaces_stale_topic(self):
        stored = {"current": "почему светлячки светятся", "snippet": "почему светлячки"}
        out = topic_tracking_for_turn("почему свет такой быстрый", stored)
        self.assertIn("быстр", out.get("current", "").lower())

    def test_anaphora_keeps_topic(self):
        stored = {"current": "почему небо голубое", "snippet": "небо"}
        out = topic_tracking_for_turn("а почему оно такое", stored)
        self.assertEqual(out.get("current"), "почему небо голубое")

    def test_short_why_keeps_anal_topic(self):
        stored = {
            "current": "почему очко анальное сжимается",
            "snippet": "анальное",
        }
        out = topic_tracking_for_turn("почему", stored)
        self.assertIn("анальн", out.get("current", "").lower())

    def test_otkuda_keeps_topic_not_city(self):
        stored = {"current": "как происходит возбуждение", "snippet": "возбуждение"}
        out = topic_tracking_for_turn("откуда", stored)
        self.assertIn("возбужд", out.get("current", "").lower())


if __name__ == "__main__":
    unittest.main()
