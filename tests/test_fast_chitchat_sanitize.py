"""Фильтр мета-рассуждений в fast-chitchat."""

import unittest

from core.brain.fast_chitchat import _sanitize_chitchat_reply


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


if __name__ == "__main__":
    unittest.main()
