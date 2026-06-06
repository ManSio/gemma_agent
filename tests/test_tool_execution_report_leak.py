"""Утечка пересказа журнала инструментов и repair новостей."""
import asyncio
from unittest.mock import AsyncMock, patch

from core.brain.text_helpers import (
    looks_like_tool_execution_report_leak,
    looks_like_tool_list_leak,
)
from core.news_reply import (
    apply_news_prefetch_fallback_if_needed,
    repair_news_tool_narration_reply,
)
from core.text_leak_scan import primary_blocking_leak_code


def test_tool_execution_report_leak_detects_russian_header() -> None:
    s = "Внешние вызовы:\n- UniversalSearch.search: запрос «новости» ответил 3 результатами"
    assert looks_like_tool_execution_report_leak(s)
    assert primary_blocking_leak_code(s) == "tool_execution_report_leak"


def test_tool_list_leak_numbered_tools() -> None:
    s = "1) Wikipedia.search_pages — поиск статей\n2) Wikipedia.get_page — содержимое"
    assert looks_like_tool_list_leak(s)
    assert looks_like_tool_execution_report_leak(s)


def test_apply_news_prefetch_replaces_tool_narration() -> None:
    leak = (
        "Внешние вызовы:\n"
        "- UniversalSearch.search: запрос «новости» ответил 2 результатами:\n"
        "1. Example — snippet"
    )
    body = "1. Reuters — US talks; 2. BBC — markets"
    out = apply_news_prefetch_fallback_if_needed(
        leak,
        search_body=body,
        user_query="Какие новости",
        task_facts={"is_news": True},
    )
    assert "Внешние вызовы" not in out
    assert "Reuters" in out or "1." in out


def test_repair_news_tool_narration_uses_search_pack() -> None:
    leak = "Внешние вызовы:\n- UniversalSearch.search: ответил 1 результатом"
    with patch(
        "core.news_reply._search_pack",
        new_callable=AsyncMock,
        return_value={
            "ok": True,
            "summary": "1. Agency — headline text",
            "results": [
                {
                    "title": "Agency headline text with enough length for filter",
                    "url": "https://reuters.com/world/example-article-2026",
                    "snippet": "Details about the event today.",
                }
            ],
        },
    ):
        fixed = asyncio.run(
            repair_news_tool_narration_reply(
                leak,
                user_query="Какие новости",
                search_body="",
                user_id="1",
            )
        )
    assert fixed
    assert "Внешние вызовы" not in fixed
