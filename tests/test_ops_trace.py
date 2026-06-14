import json
import tempfile
import unittest
from pathlib import Path

from core.ops_trace import analyze_turn, append_ops_record, read_tail, record_ops_turn


class OpsTraceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.mkdtemp()
        self._old = __import__("os").environ.get("GEMMA_OPS_TRACE_PATH")
        __import__("os").environ["GEMMA_OPS_TRACE_PATH"] = str(Path(self._dir) / "ops.jsonl")
        __import__("os").environ["OPS_TRACE_ENABLED"] = "true"

    def tearDown(self) -> None:
        import os

        if self._old is None:
            os.environ.pop("GEMMA_OPS_TRACE_PATH", None)
        else:
            os.environ["GEMMA_OPS_TRACE_PATH"] = self._old

    def test_shift_greeting_detected(self):
        issues = analyze_turn(
            user_text="почему птицы не писяют",
            assistant_text="Привет! Чем могу помочь?",
            recent_after=[
                {"role": "user", "text": "почему птицы не писяют"},
                {"role": "assistant", "text": "Привет! Чем могу помочь?"},
            ],
        )
        self.assertIn("greeting_on_substantive_question", issues)

    def test_pairing_mismatch_detected(self):
        issues = analyze_turn(
            user_text="почему звезды мерцают",
            assistant_text="ответ про птиц",
            recent_after=[
                {"role": "user", "text": "другой вопрос"},
                {"role": "assistant", "text": "ответ про птиц"},
            ],
        )
        self.assertIn("recent_user_text_mismatch", issues)

    def test_record_and_read(self):
        row = record_ops_turn(
            user_id="u1",
            group_id=None,
            channel="test",
            user_text="привет",
            assistant_text="Привет!",
            recent_before=[],
            recent_after=[
                {"role": "user", "text": "привет"},
                {"role": "assistant", "text": "Привет!"},
            ],
            archive_tail=[],
        )
        self.assertTrue(row.get("ok"))
        tail = read_tail(limit=5, user_id="u1")
        self.assertEqual(len(tail), 1)
        self.assertEqual(tail[0].get("user_text_len"), len("привет"))
        self.assertTrue(tail[0].get("user_text_hash"))
        self.assertNotIn("user_text", tail[0])


if __name__ == "__main__":
    unittest.main()
