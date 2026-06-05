"""Tests for core/compactor.py — LLM Compactor (with protect_last_n)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from core.compactor import (
    compaction_needed,
    compact_dialogue_llm,
    compact_document_llm,
    inject_dialogue_compact,
    inject_document_compact,
)


# ── compaction_needed ──


def test_compaction_not_needed_when_disabled():
    with patch("core.compactor.compactor_enabled", return_value=False):
        assert not compaction_needed(collapse_level=5, est_tokens=99999, max_budget=8000)


def test_compaction_needed_by_collapse_level():
    with patch("core.compactor.compactor_enabled", return_value=True):
        assert compaction_needed(collapse_level=3, est_tokens=100, max_budget=8000)
        assert compaction_needed(collapse_level=4, est_tokens=100, max_budget=8000)


def test_compaction_not_needed_low_collapse():
    with patch("core.compactor.compactor_enabled", return_value=True):
        assert not compaction_needed(collapse_level=2, est_tokens=100, max_budget=8000)


def test_compaction_needed_by_threshold():
    with (
        patch("core.compactor.compactor_enabled", return_value=True),
        patch("core.compactor.compactor_threshold", return_value=0.7),
        patch("core.compactor.compaction_budget_tokens", return_value=12000),
    ):
        assert compaction_needed(collapse_level=0, est_tokens=9000, max_budget=8000)
        assert not compaction_needed(collapse_level=0, est_tokens=5000, max_budget=8000)


def test_evaluate_compaction_triggers_turn_index():
    with (
        patch("core.compactor.compactor_enabled", return_value=True),
        patch("core.compactor.compactor_threshold", return_value=0.7),
        patch("core.compactor.compaction_budget_tokens", return_value=12000),
        patch("core.compactor.compactor_turn_limit", return_value=8),
    ):
        from core.compactor import evaluate_compaction_triggers

        needed, meta = evaluate_compaction_triggers(
            collapse_level=0,
            est_tokens=2000,
            dialogue_messages=[{"role": "user", "text": "hi"}] * 4,
            turn_index=10,
        )
        assert needed
        assert "session_turn_index" in meta["triggers"]


def test_build_compaction_log():
    from core.compactor import build_compaction_log

    log = build_compaction_log(
        {"needed": True, "compacted": True, "triggers": ["a"], "noise": 1}
    )
    assert log["needed"] is True
    assert "noise" not in log


def test_compaction_not_needed_when_budget_zero():
    with (
        patch("core.compactor.compactor_enabled", return_value=True),
        patch("core.compactor.compaction_budget_tokens", return_value=0),
    ):
        assert not compaction_needed(collapse_level=0, est_tokens=9999, max_budget=0)


# ── inject_dialogue_compact ──


def test_inject_dialogue_compact_sets_parts():
    parts: dict = {"recent_dialogue": [{"role": "user", "content": "old long msg"}]}
    meta: dict = {}
    inject_dialogue_compact(parts, "Краткая сводка диалога", [], meta)

    assert meta["dialogue_llm_compacted"] is True
    assert meta["dialogue_summary_len"] == len("Краткая сводка диалога")
    assert parts["dialogue_summary_compacted"] == "Краткая сводка диалога"

    rd = parts["recent_dialogue"]
    assert isinstance(rd, list) and len(rd) == 1
    assert rd[0]["role"] == "system"
    assert "[Сводка диалога]" in rd[0]["content"]


def test_inject_dialogue_compact_with_protected():
    """Когда есть protected_messages, они должны быть после сводки."""
    parts: dict = {"recent_dialogue": []}
    meta: dict = {}
    protected = [{"role": "user", "text": "protected msg"}, {"role": "assistant", "text": "protected reply"}]
    inject_dialogue_compact(parts, "сводка", protected, meta)

    rd = parts["recent_dialogue"]
    assert len(rd) == 3  # summary msg + 2 protected
    assert rd[0]["role"] == "system"
    assert rd[1] is protected[0]
    assert rd[2] is protected[1]
    assert meta["dialogue_protected_count"] == 2


def test_inject_dialogue_compact_no_summary_only_protected():
    """Когда сводка пустая, но есть protected — recent_dialogue содержит только protected."""
    parts: dict = {"recent_dialogue": []}
    meta: dict = {}
    protected = [{"role": "user", "text": "still here"}]
    inject_dialogue_compact(parts, "", protected, meta)

    rd = parts["recent_dialogue"]
    assert len(rd) == 1
    assert rd[0] is protected[0]
    assert meta["dialogue_llm_compacted"] is False  # nothing was actually compacted


def test_inject_dialogue_compact_preserves_other_keys():
    parts: dict = {"user_text": "привет", "recent_dialogue": [{"role": "user", "content": "x"}]}
    meta: dict = {}
    inject_dialogue_compact(parts, "сводка", [], meta)
    assert parts["user_text"] == "привет"


# ── inject_document_compact ──


def test_inject_document_compact_sets_parts():
    parts: dict = {"document_intake_block": "old large doc"}
    meta: dict = {}
    inject_document_compact(parts, "Сжатый документ", meta)

    assert meta["document_llm_compacted"] is True
    assert meta["document_summary_len"] == len("Сжатый документ")
    assert parts["document_intake_block"] == "Сжатый документ"


# ── compact_dialogue_llm (mocked LLM) ──


def _mock_generate(target="core.compactor.llm_generate_tiered"):
    return patch(target, new_callable=AsyncMock)


def test_compact_dialogue_llm_returns_summary():
    with _mock_generate("core.llm_tiered.llm_generate_tiered") as mock_generate:
        mock_generate.return_value = {"content": "Сводка диалога", "error": None}

        async def _run():
            messages = [
                {"role": "user", "content": "Привет!"},
                {"role": "assistant", "content": "Здравствуйте!"},
                {"role": "user", "content": "Как погода?"},
            ]
            llm = MagicMock()
            summary, protected = await compact_dialogue_llm(llm, messages)
            return summary, protected

        summary, protected = asyncio.run(_run())
        assert summary == "Сводка диалога"
        # Все 3 сообщения меньше protect_last_n(2), поэтому protected пуст,
        # и все ушли на сжатие
        mock_generate.assert_awaited_once()
        args, kwargs = mock_generate.call_args
        assert kwargs.get("tag") == "llm_compact_dialogue"


def test_compact_dialogue_llm_protects_last_n():
    """Последние 2 сообщения не должны уйти на сжатие."""
    with _mock_generate("core.llm_tiered.llm_generate_tiered") as mock_generate:
        mock_generate.return_value = {"content": "Сводка", "error": None}

        async def _run():
            messages = [
                {"role": "user", "content": "давно"},
                {"role": "assistant", "content": "было"},
                {"role": "user", "content": "последнее"},
                {"role": "assistant", "content": "сохраню"},
            ]
            llm = MagicMock()
            summary, protected = await compact_dialogue_llm(llm, messages, protect_last_n=2)
            return summary, protected

        summary, protected = asyncio.run(_run())
        assert summary == "Сводка"
        assert len(protected) == 2
        assert protected[0]["content"] == "последнее"
        assert protected[1]["content"] == "сохраню"


def test_compact_dialogue_llm_all_protected_when_small():
    """Когда сообщений меньше или равно protect_last_n — всё protected, LLM не вызывается."""
    with _mock_generate("core.llm_tiered.llm_generate_tiered") as mock_generate:
        async def _run():
            messages = [
                {"role": "user", "content": "только"},
                {"role": "assistant", "content": "пара"},
            ]
            llm = MagicMock()
            summary, protected = await compact_dialogue_llm(llm, messages, protect_last_n=2)
            return summary, protected

        summary, protected = asyncio.run(_run())
        assert summary == ""
        assert len(protected) == 2
        mock_generate.assert_not_awaited()


def test_compact_dialogue_llm_returns_empty_on_error():
    with _mock_generate("core.llm_tiered.llm_generate_tiered") as mock_generate:
        mock_generate.side_effect = RuntimeError("LLM down")

        async def _run():
            llm = MagicMock()
            summary, protected = await compact_dialogue_llm(llm, [{"role": "user", "content": "x"}] * 10)
            return summary, protected

        summary, protected = asyncio.run(_run())
        assert summary == ""


def test_compact_dialogue_llm_handles_plain_strings():
    """Plain strings (non-dict) work; protect_last_n=0 to avoid short-message guard."""
    with _mock_generate("core.llm_tiered.llm_generate_tiered") as mock_generate:
        mock_generate.return_value = {"content": "summary", "error": None}

        async def _run():
            llm = MagicMock()
            summary, protected = await compact_dialogue_llm(llm, ["plain user", "plain assistant"], protect_last_n=0)
            return summary, protected

        summary, protected = asyncio.run(_run())
        assert summary == "summary"


# ── compact_document_llm (unchanged) ──


def test_compact_document_llm_returns_summary():
    with _mock_generate("core.llm_tiered.llm_generate_tiered") as mock_generate:
        mock_generate.return_value = {"content": "Сжатая статья", "error": None}

        async def _run():
            llm = MagicMock()
            return await compact_document_llm(llm, "большой текст документа " * 200)

        result = asyncio.run(_run())
        assert result == "Сжатая статья"


def test_compact_document_llm_truncates_long_body():
    with _mock_generate("core.llm_tiered.llm_generate_tiered") as mock_generate:
        mock_generate.return_value = {"content": "ok", "error": None}

        async def _run():
            llm = MagicMock()
            await compact_document_llm(llm, "x" * 10000)

        asyncio.run(_run())
        args, kwargs = mock_generate.call_args
        prompt = kwargs.get("prompt", "")
        assert len(prompt) < 5000  # body should be truncated to ~4000


def test_compact_document_llm_uses_custom_tag():
    with _mock_generate("core.llm_tiered.llm_generate_tiered") as mock_generate:
        mock_generate.return_value = {"content": "ok", "error": None}

        async def _run():
            llm = MagicMock()
            await compact_document_llm(llm, "text", tag="llm_compact_book")

        asyncio.run(_run())
        args, kwargs = mock_generate.call_args
        assert kwargs.get("tag") == "llm_compact_book"
