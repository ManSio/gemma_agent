"""Фильтр мета-рассуждений в fast-chitchat."""

import unittest

from core.brain.fast_chitchat import _sanitize_chitchat_reply, deterministic_pure_chitchat_reply


class FastChitchatSanitizeTests(unittest.TestCase):
    def test_drops_mental_monologue_block(self):
        raw = (
            'Мысленно перебираю детали запроса. Пользователь сказал "привет". '
            "Это простое приветствие.\n"
            "По контексту видно, что это уже второй раз за диалог.\n"
            "Нужно ответить кратко, тепло, естественно."
        )
        self.assertEqual(_sanitize_chitchat_reply(raw), "")

    def test_keeps_plain_greeting(self):
        self.assertEqual(_sanitize_chitchat_reply("Привет! Рад тебя видеть."), "Привет! Рад тебя видеть.")

    def test_deterministic_greeting_no_llm(self):
        out = deterministic_pure_chitchat_reply("\u043f\u0440\u0438\u0432\u0435\u0442", "u1")
        self.assertGreater(len(out), 5)
        low = out.lower()
        self.assertTrue(
            low.startswith("\u043f\u0440\u0438\u0432\u0435\u0442") or low.startswith("\u0437\u0434\u0440\u0430\u0432\u0441\u0442\u0432\u0443\u0439")
        )

    def test_deterministic_kak_dela(self):
        out = deterministic_pure_chitchat_reply("\u043a\u0430\u043a \u0434\u0435\u043b\u0430", "u1")
        self.assertIn("?", out)


if __name__ == "__main__":
    unittest.main()
