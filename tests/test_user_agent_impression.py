"""Накопление user_agent_impression в BehaviorStore."""
from __future__ import annotations

import unittest

from core.user_agent_impression import (
    impression_excerpt_for_snapshot,
    update_user_agent_impression_in_record,
    user_agent_impression_enabled,
)


class UserAgentImpressionTests(unittest.TestCase):
    def test_updates_counters_and_summary(self):
        rec = {
            "conversation_style": "detailed",
            "session_task": {
                "last_module": "smartchat",
                "last_tool": "LawSearch.search",
                "last_tool_ok": True,
            },
            "user_agent_impression": {},
        }
        update_user_agent_impression_in_record(
            rec,
            user_id="42",
            user_text="x" * 500,
            telegram_is_admin=False,
        )
        self.assertTrue(user_agent_impression_enabled())
        imp = rec.get("user_agent_impression")
        self.assertIsInstance(imp, dict)
        self.assertEqual(int((imp.get("counters") or {}).get("turns_recorded")), 1)
        self.assertGreaterEqual(int((imp.get("counters") or {}).get("messages_long")), 1)
        av = imp.get("assistant_view") if isinstance(imp.get("assistant_view"), dict) else {}
        self.assertTrue(str(av.get("summary_ru") or "").strip())
        self.assertIn("эврист", str(av.get("disclaimer_ru") or "").lower())

    def test_excerpt_for_snapshot(self):
        rec = {
            "user_agent_impression": {
                "counters": {"turns_recorded": 3},
                "habit_tags": ["поиск и загрузка НПА"],
                "traits": ["поиск и загрузка НПА"],
                "assistant_view": {"summary_ru": "Тест.", "disclaimer_ru": "Дискл."},
                "last_updated": "2026-01-01",
            }
        }
        ex, hint = impression_excerpt_for_snapshot(rec)
        self.assertIn("assistant_view_ru", ex)
        self.assertIn("система", hint.lower())


if __name__ == "__main__":
    unittest.main()
