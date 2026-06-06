import json
import os
import unittest
from unittest.mock import AsyncMock, patch

from core.dialog_memory_recall import (
    build_recall_bundle_text,
    format_archive_for_recall,
    format_mem0_for_recall,
    read_session_digest_for_user,
)


class DialogMemoryRecallTests(unittest.TestCase):
    def test_format_mem0(self):
        s = format_mem0_for_recall([{"memory": "пользователь любит кофе"}, {"content": "второй факт"}])
        self.assertIn("Mem0", s)
        self.assertIn("кофе", s)

    def test_format_archive_search(self):
        items = [
            {"role": "user", "text": "привет про дельту", "telegram_ts": 1_700_000_000},
            {"role": "assistant", "text": "ок", "telegram_ts": 1_700_000_030},
        ]
        out = format_archive_for_recall(items, tail_n=10, query="дельт")
        self.assertIn("user:", out.lower())
        self.assertIn("дельт", out.lower())

    def test_digest_read_filter(self):
        p = os.path.join(os.path.dirname(__file__), "_tmp_session_digest_recall.jsonl")
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass
        line_u591 = json.dumps(
            {"ts": "2026-01-01T00:00:00+00:00", "user_id": "591", "turns": 2, "samples": [{"user_excerpt": "recall_marker"}]},
            ensure_ascii=False,
        )
        line_other = json.dumps(
            {"ts": "2026-01-02T00:00:00+00:00", "user_id": "999", "turns": 1, "samples": []},
            ensure_ascii=False,
        )
        with open(p, "w", encoding="utf-8") as f:
            f.write(line_other + "\n" + line_u591 + "\n")
        with patch.dict(os.environ, {"GEMMA_SESSION_DIGEST_PATH": p, "SESSION_DIGEST_ENABLED": "true"}, clear=False):
            txt = read_session_digest_for_user("591", max_records=3)
        self.assertIn("2026-01-01", txt)
        self.assertIn("recall_marker", txt)
        self.assertNotIn("999", txt)

    def test_bundle_summary_includes_blocks(self):
        ctx = {
            "dialogue_summary": "раньше обсуждали δ-игру",
            "mem0_facts": [{"memory": "факт один"}],
        }
        items = [{"role": "user", "text": "сообщение архива", "telegram_ts": 1_700_000_000}]
        with patch("core.message_archive.load_message_archive_items", return_value=items):
            with patch("core.dialog_memory_recall.read_session_digest_for_user", return_value=""):
                txt = build_recall_bundle_text(
                    user_id="42",
                    group_id=None,
                    context=ctx,
                    mode="summary",
                    archive_tail=10,
                )
        self.assertIn("Mem0", txt)
        self.assertIn("dialogue_summary", txt)
        self.assertIn("архива", txt.lower())


class DialogRecallLlmTests(unittest.IsolatedAsyncioTestCase):
    async def test_llm_subcommand_calls_brain(self):
        from modules.dialog_memory_recall.module import DialogMemoryRecallModule

        with patch.dict(os.environ, {"DIALOG_RECALL_LLM_ENABLED": "true"}, clear=False):
            with patch(
                "modules.dialog_memory_recall.module.build_slash_recall_bundle",
                return_value="STUB_FACT_LINE_1\nSTUB_FACT_LINE_2",
            ):
                with patch("modules.dialog_memory_recall.module.call_brain", new_callable=AsyncMock) as bm:
                    bm.return_value = "Краткий связный пересказ."
                    mod = DialogMemoryRecallModule()
                    out = await mod.execute(
                        {
                            "input": {"payload": "/dialog_recall llm summary"},
                            "context": {"user_id": "1", "user_facts": {}},
                        }
                    )
        self.assertEqual(out.meta.get("recall_mode"), "llm")
        self.assertIn("пересказ", out.payload)
        bm.assert_called_once()

    async def test_llm_subcommand_falls_back_to_facts_on_brain_error(self):
        from modules.dialog_memory_recall.module import DialogMemoryRecallModule

        with patch.dict(os.environ, {"DIALOG_RECALL_LLM_ENABLED": "true"}, clear=False):
            with patch(
                "modules.dialog_memory_recall.module.build_slash_recall_bundle",
                return_value="FACT_A\nFACT_B",
            ):
                with patch(
                    "modules.dialog_memory_recall.module.call_brain",
                    new_callable=AsyncMock,
                    side_effect=RuntimeError("brain down"),
                ):
                    mod = DialogMemoryRecallModule()
                    out = await mod.execute(
                        {
                            "input": {"payload": "/dialog_recall llm summary"},
                            "context": {"user_id": "1", "user_facts": {}},
                        }
                    )
        self.assertEqual(out.meta.get("llm_fallback"), "facts_only")
        self.assertIn("FACT_A", out.payload)
        self.assertIn("факты", out.payload.lower())


class DialogRecallNlTests(unittest.IsolatedAsyncioTestCase):
    async def test_plain_text_runs_summary_when_nl_enabled(self):
        from modules.dialog_memory_recall.module import DialogMemoryRecallModule

        with patch.dict(os.environ, {"DIALOG_RECALL_NL_ROUTE_ENABLED": "true"}, clear=False):
            with patch(
                "modules.dialog_memory_recall.module.build_slash_recall_bundle",
                return_value="SUMMARY_BLOCK",
            ):
                mod = DialogMemoryRecallModule()
                out = await mod.execute(
                    {
                        "input": {"payload": "Напомни что мы обсуждали"},
                        "context": {"user_id": "1", "user_facts": {}},
                    }
                )
        self.assertEqual(out.meta.get("recall_mode"), "summary")
        self.assertIn("SUMMARY_BLOCK", out.payload)


if __name__ == "__main__":
    unittest.main()
