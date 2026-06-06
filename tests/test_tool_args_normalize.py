"""Нормализация аргументов инструментов перед run_tool."""
from __future__ import annotations

from core.tool_args_normalize import normalize_brain_tool_args


def test_lawsearch_fetch_act_maps_link_to_url() -> None:
    d = normalize_brain_tool_args(
        "LawSearch.fetch_act",
        {"link": "https://law.example.com/document/test", "user_id": "1"},
    )
    assert d["url"] == "https://law.example.com/document/test"


def test_lawsearch_keyword_search_maps_q() -> None:
    d = normalize_brain_tool_args(
        "LawSearch.keyword_search",
        {"q": "налог", "user_id": "1"},
    )
    assert d["query"] == "налог"


def test_document_corpus_unified_search_maps_q() -> None:
    d = normalize_brain_tool_args(
        "DocumentCorpus.unified_search",
        {"q": "указ 95", "user_id": "1"},
    )
    assert d["query"] == "указ 95"


def test_document_corpus_outline_maps_doc_id() -> None:
    d = normalize_brain_tool_args(
        "DocumentCorpus.document_outline",
        {"id": "law:abc", "user_id": "1"},
    )
    assert d["document_id"] == "law:abc"


def test_document_corpus_resolve_original_maps_doc_id() -> None:
    d = normalize_brain_tool_args(
        "DocumentCorpus.resolve_original",
        {"doc_id": "book:x", "user_id": "1"},
    )
    assert d["document_id"] == "book:x"


def test_uka_archive_search_maps_q() -> None:
    d = normalize_brain_tool_args(
        "UserKnowledgeArchive.archive_search",
        {"q": "тишина", "user_id": "1"},
    )
    assert d["query"] == "тишина"


def test_uka_archive_read_maps_entry_id() -> None:
    eid = "a1b2c3d4e5f67890"
    d = normalize_brain_tool_args(
        "UserKnowledgeArchive.archive_read",
        {"id": eid, "user_id": "1"},
    )
    assert d["entry_id"] == eid


def test_preserves_existing_url() -> None:
    d = normalize_brain_tool_args(
        "LawSearch.fetch_act",
        {"url": "https://law-archive.example.com/document/x", "link": "https://other"},
    )
    assert d["url"] == "https://law-archive.example.com/document/x"


def test_arithmetic_maps_expr() -> None:
    d = normalize_brain_tool_args(
        "ArithmeticTool.evaluate",
        {"expr": "2+2", "user_id": "1"},
    )
    assert d["expression"] == "2+2"


def test_archive_store_maps_note_to_body() -> None:
    d = normalize_brain_tool_args(
        "UserKnowledgeArchive.archive_store",
        {"note": "hello", "user_id": "1"},
    )
    assert d["body"] == "hello"
