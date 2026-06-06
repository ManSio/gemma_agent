import unittest

from core.geo_location_reply import (
    format_telegram_location_reply,
    is_telegram_location_turn,
    try_telegram_location_reply_sync,
)
from core.geo_nearby_reply import is_nearby_request


class GeoLocationReplyTests(unittest.TestCase):
    def test_is_location_turn(self):
        meta = {"telegram_location": {"latitude": 53.74, "longitude": 27.69}}
        self.assertTrue(is_telegram_location_turn(meta, ""))

    def test_format_reply_ru(self):
        meta = {
            "telegram_location": {
                "latitude": 53.744,
                "longitude": 27.693,
                "display_name": "Гомель, Беларусь",
            }
        }
        out = format_telegram_location_reply(meta)
        self.assertIn("Принял", out)
        self.assertIn("Гомель", out)

    def test_location_intro_not_nearby_request(self):
        text = (
            "Пользователь прислал метку карты (Telegram location). "
            "Кратко опиши место. что рядом, маршрут"
        )
        self.assertFalse(is_nearby_request(text))

    def test_location_intro_gets_stub_reply(self):
        meta = {"telegram_location": {"latitude": 53.74, "longitude": 27.69}}
        text = (
            "Пользователь прислал метку карты (Telegram location). "
            "Координаты: 53.7, 27.6. Кратко опиши. что рядом"
        )
        out = try_telegram_location_reply_sync(text, meta=meta)
        self.assertTrue(out and "Принял" in out)

    def test_location_only_returns_reply(self):
        meta = {
            "telegram_location": {
                "latitude": 53.744,
                "longitude": 27.693,
                "display_name": "Минск",
            }
        }
        text = "Пользователь прислал метку карты. Координаты: 53.7, 27.6."
        out = try_telegram_location_reply_sync(text, meta=meta)
        self.assertTrue(out and "Принял" in out)


class CapitalTypoTests(unittest.TestCase):
    def test_stoitsa_to_stolitsa(self):
        from core.brain.text_helpers import normalize_capital_query_typos

        self.assertIn(
            "столица",
            normalize_capital_query_typos("стоица минска").lower(),
        )


if __name__ == "__main__":
    unittest.main()
