"""Короткий кэш повторов UrlFetch / UniversalSearch / Wikipedia на user_id (один чат — тот же user_id в боте)."""

from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

_MAX_ENTRIES_PER_USER = 64
_store: Dict[str, List[Tuple[float, str, Any]]] = {}


def _mono_now() -> float:
    return time.monotonic()


def _ttl_sec() -> float:
    try:
        v = float((os.getenv("BRAIN_TOOL_DEDUP_TTL_SEC") or "90").strip())
    except ValueError:
        v = 90.0
    return max(5.0, min(v, 600.0))


def dedup_enabled() -> bool:
    raw = (os.getenv("BRAIN_TOOL_DEDUP_ENABLED") or "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def cache_key(tool_name: str, tool_args: Dict[str, Any]) -> Optional[str]:
    if tool_name == "UrlFetch.fetch_page":
        u = tool_args.get("url")
        if not isinstance(u, str) or not u.strip():
            return None
        return f"url:{u.strip()}"
    if tool_name == "UniversalSearch.search":
        q = tool_args.get("query")
        if not isinstance(q, str) or not q.strip():
            return None
        return f"search:{q.strip().lower()[:800]}"
    if tool_name == "Wikipedia.scan":
        q = tool_args.get("query")
        if not isinstance(q, str) or not q.strip():
            return None
        lang_raw = tool_args.get("lang") if tool_args.get("lang") is not None else tool_args.get("wiki_lang")
        lang_s = str(lang_raw).strip().lower() if lang_raw is not None and str(lang_raw).strip() else ""
        if lang_s and re.fullmatch(r"[a-z]{2,12}", lang_s):
            return f"wiki:{lang_s}:{q.strip().lower()[:800]}"
        return f"wiki::{q.strip().lower()[:800]}"
    return None


def _prune(uid: str) -> None:
    t = _mono_now()
    ent = [e for e in _store.get(uid, []) if e[0] > t]
    if len(ent) > _MAX_ENTRIES_PER_USER:
        ent = ent[-_MAX_ENTRIES_PER_USER:]
    _store[uid] = ent


def lookup(user_id: str, tool_name: str, tool_args: Dict[str, Any]) -> Optional[Any]:
    if not dedup_enabled():
        return None
    ck = cache_key(tool_name, tool_args)
    if not ck:
        return None
    uid = str(user_id or "unknown")
    _prune(uid)
    ttl = _ttl_sec()
    now = _mono_now()
    for exp, k, val in reversed(_store.get(uid, [])):
        if k == ck and exp > now:
            return val
    return None


def store(user_id: str, tool_name: str, tool_args: Dict[str, Any], result: Any) -> None:
    if not dedup_enabled():
        return
    ck = cache_key(tool_name, tool_args)
    if not ck:
        return
    if isinstance(result, dict) and result.get("error"):
        return
    uid = str(user_id or "unknown")
    ttl = _ttl_sec()
    _prune(uid)
    lst = _store.setdefault(uid, [])
    lst.append((_mono_now() + ttl, ck, result))
    if len(lst) > _MAX_ENTRIES_PER_USER:
        del lst[: len(lst) - _MAX_ENTRIES_PER_USER]
