"""Integration: news hot path sources, validator fetch, generation log, self-verify."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from core.news_reply import (
    _apply_news_self_verify,
    _dialogue_rows_for_consistency,
    _emit_news_generation_log,
    _fetch_page_article,
    _return_news_with_telemetry,
    _source_context_for_verify,
    _source_from_fetched_article,
    _sources_from_search_results,
)


class NewsHotPathWiringTests(unittest.TestCase):
    def test_sources_from_search_results(self) -> None:
        rows = [{"url": "https://example.com/a", "title": "T", "snippet": "x" * 100}]
        src = _sources_from_search_results(rows)
        self.assertEqual(len(src), 1)
        self.assertEqual(src[0]["fetch_method"], "web_search")
        self.assertGreater(src[0]["parsing_confidence"], 0.0)

    def test_source_from_fetched_article_uses_confidence(self) -> None:
        src = _source_from_fetched_article(
            {"url": "https://ex.com/a", "text": "body " * 40, "parsing_confidence": 0.82},
            title="Headline",
        )
        self.assertIsNotNone(src)
        assert src is not None
        self.assertEqual(src["parsing_confidence"], 0.82)

    def test_emit_news_generation_log_writes_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "llm_usage.jsonl"
            with patch.dict(
                os.environ,
                {"GEMMA_LLM_USAGE_PERSIST": "true", "GEMMA_LLM_USAGE_PATH": str(log_path)},
                clear=False,
            ):
                _emit_news_generation_log(
                    user_id="1",
                    query="новости",
                    sources=_sources_from_search_results(
                        [{"url": "https://example.com", "title": "H", "snippet": "snippet"}]
                    ),
                    reply="ответ",
                )
            lines = log_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            row = json.loads(lines[0])
            self.assertEqual(row["type"], "news_generation")
            self.assertEqual(row["total_sources"], 1)
            self.assertNotIn("sources", row)
            self.assertNotIn("query", row)
            self.assertNotIn("reply", row)
            self.assertIn("fetch_methods_used", row)
            self.assertIn("avg_confidence", row)

    def test_return_news_with_telemetry_none_on_empty(self) -> None:
        async def run() -> None:
            self.assertIsNone(await _return_news_with_telemetry("", user_id="1", query="q"))

        asyncio.run(run())

    def test_dialogue_rows_for_consistency_role_format(self) -> None:
        rows = _dialogue_rows_for_consistency(
            [
                {"role": "user", "text": "новости"},
                {"role": "assistant", "text": "Earthquake in Turkey today with emergency measures."},
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["user"], "новости")
        self.assertIn("Earthquake", rows[0]["bot"])

    def test_return_news_with_telemetry_logs_consistency_conflict(self) -> None:
        async def run() -> None:
            prev = (
                "Событие 15 марта 2023 года в Москве. Было объявлено чрезвычайное положение "
                "в центральном районе города."
            )
            new = (
                "В Москве сейчас чрезвычайное положение 15 марта 2024 года. Ситуация отличается "
                "от прошлогодней и требует внимания."
            )
            dialogue = [{"user": "q", "bot": prev, "index": 0}]
            with tempfile.TemporaryDirectory() as td:
                log_path = Path(td) / "llm_usage.jsonl"
                with patch.dict(
                    os.environ,
                    {
                        "GEMMA_LLM_USAGE_PERSIST": "true",
                        "GEMMA_LLM_USAGE_PATH": str(log_path),
                        "NEWS_CONSISTENCY_CHECK_ENABLED": "true",
                    },
                    clear=False,
                ):
                    out = await _return_news_with_telemetry(
                        new,
                        user_id="u1",
                        query="новости",
                        sources=[],
                        recent_dialogue=dialogue,
                    )
                self.assertEqual(out, new)
                row = json.loads(log_path.read_text(encoding="utf-8").strip())
                self.assertTrue(row["consistency_checked"])
                self.assertFalse(row["consistency_ok"])
                self.assertGreaterEqual(row["consistency_conflicts_count"], 1)

        asyncio.run(run())

    def test_fetch_page_article_rejects_cloudflare_text(self) -> None:
        async def run() -> None:
            blocked = "cloudflare challenge-platform verification " + ("x" * 80)
            with patch("core.news_reply.with_retry", new_callable=AsyncMock) as wr:
                wr.return_value = {
                    "ok": True,
                    "text": blocked,
                    "url": "https://example.com/news/story.html",
                    "http_status": 200,
                    "content_type": "text/html",
                }
                with patch("core.news_reply._url_looks_like_article", return_value=True):
                    got = await _fetch_page_article(
                        "https://example.com/news/story.html",
                        user_id="u",
                        title="title",
                        timeout=5.0,
                    )
                self.assertEqual(got.get("text"), "")

        asyncio.run(run())

    def test_fetch_page_article_keeps_valid_body_with_confidence(self) -> None:
        async def run() -> None:
            body = "Valid article body about markets and policy. " * 4
            with patch("core.news_reply.with_retry", new_callable=AsyncMock) as wr:
                wr.return_value = {
                    "ok": True,
                    "text": body,
                    "url": "https://example.com/news/markets-2026.html",
                    "http_status": 200,
                    "content_type": "text/html",
                }
                with patch("core.news_reply._page_text_usable", return_value=True):
                    with patch("core.news_reply._url_looks_like_article", return_value=True):
                        got = await _fetch_page_article(
                            "https://example.com/news/markets-2026.html",
                            user_id="u",
                            title="markets",
                            timeout=5.0,
                        )
                self.assertTrue(got.get("text"))
                self.assertGreater(float(got.get("parsing_confidence") or 0), 0.0)

        asyncio.run(run())


class NewsSelfVerifyWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_source_context_for_verify(self) -> None:
        ctx = _source_context_for_verify(
            _sources_from_search_results(
                [{"url": "https://ex.com/a", "title": "Headline", "snippet": "x" * 50}]
            )
        )
        self.assertIn("Headline", ctx)
        self.assertIn("https://ex.com/a", ctx)

    async def test_apply_news_self_verify_applies_fix(self) -> None:
        sources = _sources_from_search_results(
            [{"url": "https://ex.com/a", "title": "Earthquake", "snippet": "Turkey quake"}]
        )
        with patch.dict(os.environ, {"NEWS_SELF_VERIFY_ENABLED": "true"}, clear=False):
            with patch(
                "core.brain.self_verify_pass.run_self_verify",
                new_callable=AsyncMock,
                return_value="fix: Verified summary only from sources.",
            ):
                with patch(
                    "core.brain.self_verify_pass.self_verify_fix_quality",
                    return_value=True,
                ):
                    with patch(
                        "core.openrouter_provider.get_openrouter_provider",
                        return_value=object(),
                    ):
                        out, ver = await _apply_news_self_verify(
                            "Hallucinated extra president name.",
                            user_query="новости",
                            sources=sources,
                        )
        self.assertTrue(out.startswith("Verified"))
        self.assertTrue(ver.startswith("fix:"))

    async def test_emit_log_records_self_verify_fields(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "llm_usage.jsonl"
            with patch.dict(
                os.environ,
                {"GEMMA_LLM_USAGE_PERSIST": "true", "GEMMA_LLM_USAGE_PATH": str(log_path)},
                clear=False,
            ):
                _emit_news_generation_log(
                    user_id="1",
                    query="новости",
                    sources=[],
                    reply="ok",
                    self_verify_run=True,
                    self_verify_result="ok",
                )
            row = json.loads(log_path.read_text(encoding="utf-8").strip())
            self.assertTrue(row["self_verify_run"])
            self.assertEqual(row["self_verify_result"], "ok")


if __name__ == "__main__":
    unittest.main()
