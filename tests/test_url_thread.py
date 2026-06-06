import unittest

from core.url_thread import gather_urls_chronological_for_brain, user_signals_url_content_fetch


class UrlThreadTests(unittest.TestCase):
    def test_gather_from_dialogue_and_user(self):
        recent = [
            {"role": "user", "text": "смотри https://docs.example.com/a"},
            {"role": "assistant", "text": "ок"},
        ]
        u = gather_urls_chronological_for_brain(
            "можешь скачать документацию",
            recent,
            "",
        )
        self.assertTrue(u)
        self.assertEqual(u[-1], "https://docs.example.com/a")

    def test_signal_ru(self):
        self.assertTrue(
            user_signals_url_content_fetch(
                "можешь скачать документацию",
                ["https://x.com/y"],
            )
        )
        self.assertFalse(user_signals_url_content_fetch("просто привет", ["https://x.com/y"]))
        self.assertTrue(user_signals_url_content_fetch("https://x.com/y", ["https://x.com/y"]))


if __name__ == "__main__":
    unittest.main()
