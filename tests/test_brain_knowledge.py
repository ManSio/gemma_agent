import unittest

from core.brain import _mask_pii_text, _summarize_knowledge_hint


class BrainKnowledgeTests(unittest.TestCase):
    def test_mask_pii_text(self):
        src = "mail me: user@example.com or +1 (555) 111-2233"
        out = _mask_pii_text(src)
        self.assertNotIn("user@example.com", out)
        self.assertNotIn("111-2233", out)
        self.assertIn("<email>", out)
        self.assertIn("<phone>", out)

    def test_summarize_knowledge_hint_limits_and_shape(self):
        hint = {
            "policy": "fresh_trusted_tagged",
            "confidence": 0.9123,
            "selected": [
                {
                    "source": "facts:city",
                    "tags": ["city", "profile"],
                    "content": "city=Moscow, contact user@example.com",
                },
                {
                    "source": "dialogue:1",
                    "tags": ["conversation"],
                    "content": "Call me at +7 999 123 45 67 for details",
                },
            ],
        }
        out = _summarize_knowledge_hint(hint, max_items=2, max_chars=220)
        self.assertIn("policy=fresh_trusted_tagged", out)
        self.assertIn("entries=2", out)
        self.assertIn("<email>", out)
        self.assertIn("<phone>", out)
        self.assertLessEqual(len(out), 220)


if __name__ == "__main__":
    unittest.main()
