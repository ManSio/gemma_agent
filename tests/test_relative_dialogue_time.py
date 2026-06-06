import unittest
from datetime import datetime, timezone

from core.relative_dialogue_time import (
    filter_archive_items_by_unix_window,
    merge_archive_and_recent_ts,
    parse_named_month_window_unix,
    parse_recall_time_window_unix,
    parse_relative_window_unix,
    recall_query_wants_earliest,
    user_asks_relative_dialogue_time,
)


class RelativeDialogueTimeTests(unittest.TestCase):
    def test_user_asks_detects_yesterday_morning_ru(self):
        self.assertTrue(
            user_asks_relative_dialogue_time("Что мы обсуждали вчера утром про дельту?")
        )

    def test_user_asks_week_ago(self):
        self.assertTrue(user_asks_relative_dialogue_time("напомни что писал неделю назад"))

    def test_user_asks_rejects_bare_clock(self):
        self.assertFalse(user_asks_relative_dialogue_time("который час"))

    def test_parse_yesterday_morning_minsk(self):
        ref = datetime(2026, 5, 10, 8, 0, 0, tzinfo=timezone.utc)
        p = parse_relative_window_unix(
            "вчера утром что было",
            user_facts={"timezone": "Europe/Minsk"},
            reference_utc=ref,
        )
        self.assertIsNotNone(p)
        start_u, end_u, label = p
        self.assertLess(start_u, end_u)
        self.assertIn("2026-05-09", label)
        self.assertIn("6,12", label.replace(" ", ""))

    def test_parse_week_ago_full_day_utc_fallback(self):
        ref = datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc)
        p = parse_relative_window_unix(
            "неделю назад обсуждали",
            user_facts={},
            reference_utc=ref,
        )
        self.assertIsNotNone(p)
        s, e, label = p
        self.assertLess(s, e)
        self.assertIn("2026-05-03", label)

    def test_merge_dedupes(self):
        arch = [{"role": "user", "text": "a", "telegram_ts": 100}]
        recent = [{"role": "user", "text": "a", "telegram_ts": 100}]
        m = merge_archive_and_recent_ts(arch, recent)
        self.assertEqual(len(m), 1)

    def test_no_false_month_in_smartphone_ru(self):
        self.assertIsNone(
            parse_named_month_window_unix(
                "купил смартфон вчера",
                user_facts={},
                reference_utc=datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc),
            )
        )

    def test_parse_april_window_may_anchor(self):
        ref = datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc)
        p = parse_named_month_window_unix(
            "найди в апреле любую запись",
            user_facts={"timezone": "Europe/Minsk"},
            reference_utc=ref,
        )
        self.assertIsNotNone(p)
        s, e, label = p  # type: ignore[misc]
        self.assertLess(s, e)
        self.assertIn("month=2026-04", label)

    def test_parse_recall_prefers_relative_days_then_month(self):
        ref = datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc)
        p = parse_recall_time_window_unix(
            "что говорил 40 дней назад",
            user_facts={"timezone": "Europe/Moscow"},
            reference_utc=ref,
        )
        self.assertIsNotNone(p)
        _s, _e, label = p  # type: ignore[misc]
        self.assertIn("2026-03-30", label)

    def test_filter_window_earliest_vs_latest(self):
        items = [
            {"role": "user", "text": "first", "telegram_ts": 100},
            {"role": "user", "text": "mid", "telegram_ts": 200},
            {"role": "user", "text": "last", "telegram_ts": 300},
        ]
        old = filter_archive_items_by_unix_window(items, 50, 400, max_lines=2, newest_first=True)
        self.assertEqual([m["text"] for m in old], ["mid", "last"])
        early = filter_archive_items_by_unix_window(items, 50, 400, max_lines=2, newest_first=False)
        self.assertEqual([m["text"] for m in early], ["first", "mid"])

    def test_recall_query_wants_earliest(self):
        self.assertTrue(recall_query_wants_earliest("любую первую запись в апреле"))
        self.assertFalse(recall_query_wants_earliest("что вчера писали"))


if __name__ == "__main__":
    unittest.main()
