"""Tests for core/context_compression.py — FIFO compression + protect_last_n."""

import unittest
from unittest.mock import patch

from core.context_compression import compress_dialogue_summary, compress_recent_dialogue


class ContextCompressionTests(unittest.TestCase):
    def test_recent_dialogue_fifo_and_clip(self):
        """FIFO: keep last N messages, clip assistant, no dedup."""
        rows = [
            {"role": "user", "text": "ok"},
            {"role": "user", "text": "ok"},  # duplicates NOT removed (no dedup)
            {"role": "assistant", "text": "A" * 2000},
        ]
        with patch.dict(
            "os.environ",
            {
                "CONTEXT_COMPRESSION_ENABLED": "true",
                "CONTEXT_RECENT_KEEP_MESSAGES": "10",
                "CONTEXT_ASSISTANT_CLIP_CHARS": "120",
                "CONTEXT_PROTECT_LAST_N": "0",  # no protection for this test
            },
            clear=False,
        ):
            out = compress_recent_dialogue(rows)
        # All 3 rows kept (no dedup); assistant text clipped
        self.assertEqual(len(out), 3)
        self.assertLessEqual(len(str(out[-1].get("text") or "")), 120)

    def test_protect_last_n_keeps_recent_verbatim(self):
        """Последние protect_last_n сообщений не обрезаются."""
        rows = [
            {"role": "user", "text": "A" * 500},
            {"role": "user", "text": "B" * 500},
        ]
        with patch.dict(
            "os.environ",
            {
                "CONTEXT_COMPRESSION_ENABLED": "true",
                "CONTEXT_RECENT_KEEP_MESSAGES": "5",
                "CONTEXT_USER_CLIP_CHARS": "80",
                "CONTEXT_PROTECT_LAST_N": "1",
            },
            clear=False,
        ):
            out = compress_recent_dialogue(rows)
        self.assertEqual(len(out), 2)
        # Первое сообщение обрезано с 500 до 80
        self.assertEqual(len(out[0]["text"]), 80)
        # Второе (последнее) — verbatim
        self.assertEqual(out[1]["text"], rows[1]["text"])

    def test_protect_last_n_zero_clips_everything(self):
        """protect_last_n=0 — все сообщения могут быть обрезаны (поведение как раньше)."""
        rows = [
            {"role": "user", "text": "A" * 500},
            {"role": "user", "text": "B" * 500},
        ]
        with patch.dict(
            "os.environ",
            {
                "CONTEXT_COMPRESSION_ENABLED": "true",
                "CONTEXT_RECENT_KEEP_MESSAGES": "5",
                "CONTEXT_USER_CLIP_CHARS": "80",
                "CONTEXT_PROTECT_LAST_N": "0",
            },
            clear=False,
        ):
            out = compress_recent_dialogue(rows)
        self.assertEqual(len(out), 2)
        for row in out:
            self.assertLessEqual(len(row["text"]), 80)

    def test_protect_last_n_gt_message_count(self):
        """Если защищённых сообщений больше чем всего — все verbatim."""
        rows = [
            {"role": "user", "text": "A" * 500},
            {"role": "user", "text": "B" * 500},
        ]
        with patch.dict(
            "os.environ",
            {
                "CONTEXT_COMPRESSION_ENABLED": "true",
                "CONTEXT_RECENT_KEEP_MESSAGES": "5",
                "CONTEXT_USER_CLIP_CHARS": "50",
                "CONTEXT_PROTECT_LAST_N": "10",  # больше чем всего сообщений
            },
            clear=False,
        ):
            out = compress_recent_dialogue(rows)
        self.assertEqual(len(out), 2)
        # Все сохранены verbatim
        self.assertEqual(out[0]["text"], rows[0]["text"])
        self.assertEqual(out[1]["text"], rows[1]["text"])

    def test_summary_limit(self):
        src = ("line\n" * 40) + ("x" * 2000)
        with patch.dict(
            "os.environ",
            {"CONTEXT_COMPRESSION_ENABLED": "true", "CONTEXT_SUMMARY_MAX_CHARS": "300"},
            clear=False,
        ):
            out = compress_dialogue_summary(src)
        self.assertLessEqual(len(out), 300)
        self.assertTrue(out)


if __name__ == "__main__":
    unittest.main()
