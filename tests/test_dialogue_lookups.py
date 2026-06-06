import unittest
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore

from core.dialogue_lookups import (
    build_dialogue_lookup_hint_for_llm,
    user_asks_past_message_with_clock,
)


@unittest.skipUnless(ZoneInfo is not None, "zoneinfo required")
class DialogueLookupsTests(unittest.TestCase):
    def test_trigger_phrases(self):
        self.assertTrue(
            user_asks_past_message_with_clock("какое я тебе сообщение прислал в 12:52")
        )
        self.assertFalse(user_asks_past_message_with_clock("встреча в 12:52 ок?"))
        self.assertFalse(user_asks_past_message_with_clock("как дела"))

    def test_finds_recent_by_local_time(self):
        ts = int(datetime(2026, 5, 2, 12, 52, 0, tzinfo=ZoneInfo("Europe/Minsk")).timestamp())
        recent = [{"role": "user", "telegram_ts": ts, "text": "КРОКОДИЛ-774"}]
        facts = {"country": "BY"}
        h = build_dialogue_lookup_hint_for_llm(
            "какое сообщение в 12:52",
            recent_messages=recent,
            dialogue_summary="",
            user_facts=facts,
        )
        self.assertIn("КРОКОДИЛ", h)
        self.assertIn("ts_unix=", h)

    def test_empty_when_no_match(self):
        ts = int(datetime(2026, 5, 2, 10, 0, 0, tzinfo=ZoneInfo("Europe/Minsk")).timestamp())
        recent = [{"role": "user", "telegram_ts": ts, "text": "другое"}]
        h = build_dialogue_lookup_hint_for_llm(
            "какое сообщение в 12:52",
            recent_messages=recent,
            dialogue_summary="",
            user_facts={"country": "BY"},
        )
        self.assertIn("нет user-сообщения", h)

    def test_scans_dialogue_summary_overflow(self):
        ts = int(datetime(2026, 5, 2, 12, 52, 7, tzinfo=ZoneInfo("Europe/Minsk")).timestamp())
        summary = f"user ts={ts}:старое из сводки"
        h = build_dialogue_lookup_hint_for_llm(
            "что я писал в 12:52",
            recent_messages=[],
            dialogue_summary=summary,
            user_facts={"country": "BY"},
        )
        self.assertIn("старое из сводки", h)

    def test_archive_list_finds_when_recent_empty(self):
        ts = int(datetime(2026, 5, 2, 12, 52, 0, tzinfo=ZoneInfo("Europe/Minsk")).timestamp())
        arch = [{"role": "user", "telegram_ts": ts, "text": "только в архиве"}]
        h = build_dialogue_lookup_hint_for_llm(
            "какое сообщение в 12:52",
            recent_messages=[],
            dialogue_summary="",
            user_facts={"country": "BY"},
            archive_messages=arch,
        )
        self.assertIn("только в архиве", h)
        self.assertIn("архив", h.lower())


if __name__ == "__main__":
    unittest.main()
