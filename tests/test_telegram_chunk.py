import unittest

from core.telegram_util import chunk_text


class TelegramChunkTests(unittest.TestCase):
    def test_single_chunk_unchanged(self):
        s = "short"
        self.assertEqual(chunk_text(s, limit=4000), [s])

    def test_soft_break_prefers_space_not_mid_word(self):
        words = ["слово"] * 600
        s = " ".join(words)
        parts = chunk_text(s, limit=500)
        self.assertGreater(len(parts), 1)
        for p in parts:
            self.assertLessEqual(len(p), 500, msg=f"len={len(p)}")
        body0 = parts[0]
        self.assertTrue(body0.endswith("слово") or body0.endswith("слово "))

    def test_continuation_header_has_correct_total(self):
        s = "a" * 300 + "\n" + "b" * 300 + "\n" + "c" * 300
        parts = chunk_text(s, limit=280)
        self.assertGreaterEqual(len(parts), 2)
        if len(parts) > 1:
            self.assertIn("часть 2/", parts[1])
            self.assertIn(f"/{len(parts)}", parts[1])


if __name__ == "__main__":
    unittest.main()
