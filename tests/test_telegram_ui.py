import os
import unittest
from unittest.mock import patch

from core.telegram_ui import (
    code_block_html,
    esc,
    format_admin_logs_html,
    format_facts_html,
    format_llm_usage_html,
    format_me_html,
    format_mem0_facts_html,
    format_psych_html,
    format_twin_html,
    format_unified_health_html,
    split_html_message,
)


class TelegramUiTests(unittest.TestCase):
    def test_format_psych_no_raw_iso_microseconds(self):
        prof = {
            "last_analysis": {
                "sentiment": "neutral",
                "stress_signals": False,
                "keywords": [],
                "analyzed_at": "2026-05-02T14:30:07.422423+00:00",
                "message_length": 5,
            },
            "stress_streak": 0,
            "updated_at": "2026-05-02T14:30:07.422423+00:00",
        }
        with patch.dict(os.environ, {"GEMMA_REPORT_TIMEZONE": ""}, clear=False):
            h = format_psych_html(prof)
        self.assertIn("14:30", h)
        self.assertIn("02.05.2026", h)
        self.assertNotIn("422423", h)
        self.assertNotIn("T14:30", h)

    def test_format_psych_compact_time_no_iana_when_tz_set(self):
        prof = {
            "last_analysis": {
                "sentiment": "neutral",
                "stress_signals": False,
                "keywords": [],
                "analyzed_at": "2026-05-10T17:19:00+00:00",
                "message_length": 61,
            },
            "stress_streak": 0,
            "updated_at": "2026-05-10T17:19:00+00:00",
        }
        with patch.dict(os.environ, {"GEMMA_REPORT_TIMEZONE": "Europe/Minsk"}, clear=False):
            h = format_psych_html(prof)
        self.assertNotIn("Europe/Minsk", h)
        self.assertNotIn("UTC+3", h)
        self.assertIn("20:19", h)

    def test_format_me_no_angle_brackets_raw(self):
        h = format_me_html(
            {"user_id": "1", "facts": {"name": "A <b>x</b>"}, "preferences": {"tone": "ok"}}
        )
        self.assertIn("A &lt;b&gt;x&lt;/b&gt;", h)
        self.assertNotIn("<b>x</b>", h)
        self.assertIn("blockquote", h)

    def test_format_facts_humanized_not_raw_python(self):
        h = format_facts_html(
            {
                "facts": {
                    "city": "Минск",
                    "country": "Беларусь",
                    "interests": ["играть в World of Tanks", "боты"],
                    "timezone": "Europe/Moscow",
                },
                "facts_meta": {
                    "country": {
                        "updated_at": "2026-05-10T17:33:33.082042+00:00",
                        "expires_at": "2027-05-10T17:33:33.082042+00:00",
                        "revoked": False,
                        "source": "message_extract",
                        "confidence": 0.9500000000000001,
                    },
                    "currency": {"revoked": True, "revoked_at": "2026-05-05T20:28:31.405814"},
                },
            }
        )
        self.assertIn("Что запомнили", h)
        self.assertIn("Подробности записи", h)
        self.assertIn("Страна", h)
        self.assertIn("Беларусь", h)
        self.assertIn("Интересы", h)
        self.assertIn("World of Tanks", h)
        self.assertNotIn("['играть", h)
        self.assertNotIn("Europe/Moscow", h)
        self.assertIn("Уверенность", h)
        self.assertIn("95%", h)
        self.assertIn("отозвано", h)
        self.assertIn("авто · из сообщения", h)
        self.assertNotIn("confidence", h)
        self.assertNotIn("message_extract", h)

    def test_format_me_prefs_russian_labels(self):
        h = format_me_html(
            {
                "user_id": "42",
                "facts": {},
                "preferences": {
                    "explanation_style": "mixed",
                    "tone": "balanced",
                    "verbosity": "concise",
                },
            }
        )
        self.assertIn("Смешанный", h)
        self.assertIn("Умеренный", h)
        self.assertIn("Краткие", h)
        self.assertNotIn("mixed", h)
        self.assertNotIn("balanced", h)
        self.assertNotIn("concise", h)

    def test_split_html(self):
        parts = split_html_message("a\n\nb\n\nc", limit=5)
        self.assertGreaterEqual(len(parts), 1)

    def test_split_html_single_huge_blob_uses_chunk_text(self):
        words = ["слово"] * 600
        blob = " ".join(words)
        parts = split_html_message(blob, limit=500)
        self.assertGreater(len(parts), 1)
        for p in parts:
            self.assertLessEqual(len(p), 500)

    def test_split_html_merges_blockquote_fragments(self):
        """Разбиение по \\n\\n не должно оставлять открытый <blockquote> без закрытия."""
        text = "<blockquote>\n\nopen\n\nclose\n\n</blockquote>"
        parts = split_html_message(text, limit=12)
        self.assertEqual(len(parts), 1)
        self.assertIn("</blockquote>", parts[0])

    def test_split_html_merges_pre_fragments(self):
        """Не рвать <pre> посередине пустыми абзацами — иначе Telegram «нет закрывающего pre»."""
        text = "<pre>\n\nline1\n\nline2\n\n</pre>"
        parts = split_html_message(text, limit=10)
        self.assertEqual(len(parts), 1)
        self.assertIn("</pre>", parts[0])

    def test_format_unified_health_escapes(self):
        snap = {
            "ts": "t",
            "integrity": {"ok": False, "issues": ["<script>"]},
            "evaluate": {},
            "degradation_summary": {},
            "resilience": {},
            "backups_recent": [],
        }
        h = format_unified_health_html(snap, max_backup_rows=2)
        self.assertIn("&lt;script&gt;", h)
        self.assertNotIn("<script>", h)

    def test_format_unified_health_external_failures(self):
        snap = {
            "ts": "t",
            "integrity": {"ok": True, "issues": []},
            "external_services": {
                "failures": [
                    {
                        "service": "openrouter",
                        "user_message": "HTTP 401",
                        "source": "connectivity",
                    }
                ],
                "failure_messages": ["openrouter: HTTP 401"],
            },
            "evaluate": {},
            "degradation_summary": {},
            "resilience": {},
            "backups_recent": [],
        }
        h = format_unified_health_html(snap, max_backup_rows=2)
        self.assertIn("Внешние API", h)
        self.assertIn("openrouter", h)
        self.assertIn("401", h)

    def test_format_admin_logs_escapes_pre(self):
        h = format_admin_logs_html("line <>&", 3)
        self.assertIn("&lt;", h)
        self.assertIn("<pre>", h)

    def test_code_block_html(self):
        h = code_block_html("a < b")
        self.assertTrue(h.startswith("<pre>") and h.endswith("</pre>"))
        self.assertIn("a &lt; b", h)

    def test_format_llm_usage_pre_tables(self):
        agg = {
            "period_days": 30.0,
            "window_records": 3,
            "completions_ok": 3,
            "completions_fail": 0,
            "total_tokens": 100,
            "prompt_tokens": 80,
            "completion_tokens": 20,
            "avg_tokens_per_ok": 33.3,
            "cost_sum": 0.001,
            "paid_completions": 2,
            "free_completions": 1,
            "daily_avg_cost": 0.0001,
            "monthly_est_cost": 0.003,
            "daily_avg_tokens": 10.0,
            "monthly_est_tokens": 300,
            "log_path": "data/<x>.jsonl",
            "by_kind": {"chat": {"n": 3, "tokens": 100, "cost": 0.001}},
            "sparkline_tokens": [1, 2, 3, 0, 0, 0, 10],
            "sparkline_cost": [0.0, 0.001, 0.0, 0.0, 0.0, 0.0, 0.002],
            "sparkline_days": ["2026-05-01", "2026-05-07"],
        }
        h = format_llm_usage_html(agg, session_cost_usd=0.005, top_rows=None)
        self.assertIn("LLM · OpenRouter", h)
        self.assertGreaterEqual(h.count("<pre>"), 4)
        self.assertIn("вызовы", h)
        self.assertIn("&lt;x&gt;", h)  # путь в &lt;pre&gt; экранируется

    def test_format_llm_usage_shows_zero_counts(self):
        agg = {
            "period_days": 7,
            "window_records": 0,
            "completions_ok": 0,
            "completions_fail": 0,
            "total_tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "avg_tokens_per_ok": 0.0,
            "cost_sum": 0.0,
            "paid_completions": 0,
            "free_completions": 0,
            "daily_avg_cost": 0.0,
            "monthly_est_cost": 0.0,
            "daily_avg_tokens": 0.0,
            "monthly_est_tokens": 0.0,
        }
        h = format_llm_usage_html(agg, top_rows=None)
        first_pre = h.split("<pre>", 1)[1].split("</pre>", 1)[0]
        for needle in ("Записей в окне", "Успешных compl.", "Ошибок compl."):
            hit = [ln for ln in first_pre.splitlines() if needle in ln]
            self.assertEqual(len(hit), 1, msg=first_pre)
            self.assertTrue(hit[0].rstrip().endswith("0"), msg=hit[0])

    def test_format_psych_human_not_json(self):
        h = format_psych_html(
            {
                "last_analysis": {
                    "sentiment": "neutral",
                    "stress_signals": False,
                    "keywords": ["sleep"],
                    "analyzed_at": "2026-05-03T12:00:00+00:00",
                    "message_length": 10,
                },
                "stress_streak": 0,
                "updated_at": "2026-05-03T12:00:00+00:00",
            }
        )
        self.assertIn("нейтральная", h)
        self.assertIn("Тональность", h)
        self.assertNotIn("&quot;last_analysis&quot;", h)
        self.assertNotIn("{", h)

    def test_format_twin_location_ru(self):
        h = format_twin_html({"user_id": "591", "location": {"city": None, "country": "X"}})
        self.assertIn("Цифровой двойник", h)
        self.assertIn("Локация", h)
        self.assertIn("Страна", h)
        self.assertIn("X", h)
        self.assertNotIn("&quot;user_id&quot;", h)

    def test_format_mem0_facts_escapes_html(self):
        h = format_mem0_facts_html(
            [{"type": "mem0", "content": "a <script>x</script>", "id": "id-1"}]
        )
        self.assertIn("&lt;script&gt;", h)
        self.assertNotIn("<script>", h)
        self.assertIn("id-1", h)


if __name__ == "__main__":
    unittest.main()
