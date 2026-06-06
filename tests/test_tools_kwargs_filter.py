import asyncio

import pytest

from core.tools import filter_kwargs_for_callable


class _StubModule:
    async def search(self, query: str, source: str = "etal"):
        return {"query": query, "source": source}

    async def swallow(self, **kwargs):
        return kwargs


def test_filter_drops_unknown_and_user_id_if_not_in_signature() -> None:
    m = _StubModule()
    out = filter_kwargs_for_callable(
        m.search,
        {"query": "право", "user_id": "1", "language": "ru", "bogus": True},
    )
    assert out == {"query": "право"}
    assert "user_id" not in out


def test_filter_passes_through_var_keyword() -> None:
    m = _StubModule()
    out = filter_kwargs_for_callable(m.swallow, {"user_id": "9", "x": 1})
    assert out == {"user_id": "9", "x": 1}


def test_run_tool_accepts_kwarg_name_without_collision(monkeypatch) -> None:
    """LLM часто шлёт name=…; не должен конфликтовать с именем инструмента в run_tool."""
    monkeypatch.setenv("USER_KNOWLEDGE_ARCHIVE_ENABLED", "false")
    import core.tools as tools_mod

    tools_mod._tools_scan_done = False
    tools_mod.TOOLS.clear()
    out = asyncio.run(
        tools_mod.run_tool(
            "UserKnowledgeArchive.archive_read",
            name="Указ 95",
            entry_id="0123456789abcdef",
            user_id="1",
        )
    )
    assert isinstance(out, dict)
    assert out.get("skipped") is True or "error" in out


def test_run_tool_unknown_lawsearch_returns_error(monkeypatch) -> None:
    monkeypatch.setenv("TOOLS_FILTER_KWARGS", "true")
    import core.tools as tools_mod

    tools_mod._tools_scan_done = False
    tools_mod.TOOLS.clear()
    out = asyncio.run(
        tools_mod.run_tool(
            "LawSearch.search",
            query="ab",
            source="etal",
            user_id="telegram-user",
            extra_unused="drop-me",
        )
    )
    assert isinstance(out, dict)
    assert "unknown tool" in str(out.get("error") or "")
