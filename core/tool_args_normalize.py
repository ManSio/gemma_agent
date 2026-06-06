"""
Нормализация аргументов TOOL_CALL перед run_tool: синонимы полей от LLM → имена параметров методов *Module.
"""
from __future__ import annotations

import re
from typing import Any, Dict


def _first_str(d: Dict[str, Any], keys: tuple[str, ...]) -> str:
    for k in keys:
        v = d.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return ""


def _looks_like_url(s: str) -> bool:
    t = (s or "").strip()
    return t.startswith("http://") or t.startswith("https://")


def normalize_brain_tool_args(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(args, dict):
        return {}
    out = dict(args)
    tn = (tool_name or "").strip()

    if tn == "ArithmeticTool.evaluate":
        if not _first_str(out, ("expression",)):
            ex = _first_str(out, ("expr", "formula", "math", "calc", "q", "query"))
            if ex:
                out["expression"] = ex

    if tn == "UserKnowledgeArchive.archive_store":
        if not _first_str(out, ("body",)):
            b = _first_str(
                out,
                ("text", "content", "note", "message", "memo", "summary", "value", "data"),
            )
            if b:
                out["body"] = b
        if not _first_str(out, ("title",)):
            tl = _first_str(out, ("subject", "heading", "label", "note_title"))
            if tl:
                out["title"] = tl

    if tn == "LawSearch.fetch_act":
        if not _first_str(out, ("url",)):
            u = _first_str(
                out,
                (
                    "link",
                    "href",
                    "document_url",
                    "doc_url",
                    "act_url",
                    "pravo_url",
                    "page_url",
                    "src",
                ),
            )
            if u and _looks_like_url(u):
                out["url"] = u

    if tn in {"LawSearch.search", "LawSearch.keyword_search", "DocumentCorpus.unified_search"}:
        if not _first_str(out, ("query",)):
            q = _first_str(out, ("q", "text", "search", "term", "keywords", "keyword"))
            if q:
                out["query"] = q

    if tn in {"DocumentCorpus.document_outline", "DocumentCorpus.resolve_original"}:
        if not _first_str(out, ("document_id",)):
            did = _first_str(out, ("id", "doc_id", "documentId"))
            if did:
                out["document_id"] = did

    if tn == "UserKnowledgeArchive.archive_read":
        if not _first_str(out, ("entry_id",)):
            eid = _first_str(out, ("entryId", "id", "archive_id", "entry", "entryID"))
            eid = eid.strip().lower()
            if eid and re.fullmatch(r"[0-9a-f]{16}", eid):
                out["entry_id"] = eid

    if tn == "UserKnowledgeArchive.archive_list":
        if not _first_str(out, ("query",)):
            q = _first_str(out, ("q", "text", "search", "title", "name"))
            if q:
                out["query"] = q

    if tn == "UserKnowledgeArchive.archive_search":
        if not _first_str(out, ("query",)):
            q = _first_str(out, ("q", "text", "search", "term", "keywords", "keyword"))
            if q:
                out["query"] = q

    if tn == "UserKnowledgeArchive.personal_library_list":
        if not _first_str(out, ("query",)):
            q = _first_str(out, ("q", "text", "search", "filename", "name"))
            if q:
                out["query"] = q

    if tn == "UserKnowledgeArchive.personal_library_read":
        if not _first_str(out, ("filename",)):
            fn = _first_str(out, ("file", "name", "path", "stem"))
            if fn:
                out["filename"] = fn

    return out
