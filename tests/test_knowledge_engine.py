import unittest

from core.knowledge_engine import KnowledgeEngine


class KnowledgeEngineTests(unittest.TestCase):
    def test_select_for_intent(self):
        ke = KnowledgeEngine()
        ke.ingest("doc1", "weather basics", version="1", tags=["weather"], trust=0.9)
        ke.ingest("doc2", "math notes", version="1", tags=["math"], trust=0.4)
        out = ke.select_for_intent("weather")
        self.assertEqual(out["policy"], "fresh_trusted_tagged")
        self.assertTrue(out["selected"])
        self.assertIn("weather", out["selected"][0]["tags"])
        self.assertGreater(out["confidence"], 0.0)

    def test_ingest_context_sources(self):
        ke = KnowledgeEngine()
        added = ke.ingest_context_sources(
            context={
                "user_facts": {"city": "Moscow", "language": "ru"},
                "mem0_facts": ["likes python", "works remotely"],
                "topic_tracking": {"active_topic": "weather"},
                "recent_dialogue": ["hi", "weather in my city"],
            }
        )
        self.assertGreaterEqual(added, 6)
        out = ke.select_for_intent("city")
        self.assertTrue(out["selected"])
        self.assertEqual(out["policy"], "fresh_trusted_tagged")


if __name__ == "__main__":
    unittest.main()
