"""
Semantic LLM Step-Cache — caches LLM responses to avoid redundant API calls.
v2.0.0: adds reasoning-cache with subject + memory_state in cache key.
v2.11.0: adds SQLite-backed persistent cache with make_cache_key/get/set/invalidate_on_reset.
Controlled by token_efficiency.yml (token_efficiency.cache).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Optional

from core.token_efficiency import cache_enabled, cache_ttl_seconds, cache_max_entries

logger = logging.getLogger(__name__)

LLM_CACHE_VERSION = "2.11.1"

# ── SQLite persistent cache ──

_sqlite_lock = threading.Lock()
_sqlite_conn: Optional[sqlite3.Connection] = None


def _sqlite_db_path() -> Path:
    root = os.getenv("GEMMA_PROJECT_ROOT") or os.getcwd()
    override = os.getenv("LLM_CACHE_DB_PATH")
    if override:
        p = Path(override)
    else:
        p = Path(root) / "data" / "runtime" / "llm_cache.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _ensure_sqlite() -> sqlite3.Connection:
    global _sqlite_conn
    if _sqlite_conn is not None:
        return _sqlite_conn
    with _sqlite_lock:
        if _sqlite_conn is not None:
            return _sqlite_conn
        path = _sqlite_db_path()
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS llm_cache ("
            "  key TEXT PRIMARY KEY,"
            "  response TEXT NOT NULL,"
            "  ts REAL NOT NULL,"
            "  ttl REAL NOT NULL"
            ")"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_llm_cache_ts ON llm_cache(ts)"
        )
        conn.commit()
        _sqlite_conn = conn
        return conn


def _default_ttl() -> float:
    try:
        return float(os.getenv("LLM_CACHE_TTL_SEC", "21600"))
    except ValueError:
        return 21600.0  # 6 hours

def _proxy_cache_enabled() -> bool:
    """Check if proxy-level cache is enabled. Checks both token_efficiency and env flag."""
    from core.token_efficiency import cache_enabled
    if cache_enabled():
        return True
    return os.getenv("LLM_PROXY_CACHE_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


def make_cache_key(context: Dict[str, Any], user_input: str, model_name: str) -> str:
    """Build cache key from normalized user input + digest of stitched context.
    Deterministic — no session_id, timestamps, counters, or dynamic fields."""
    import hashlib

    def _normalize(text: str) -> str:
        return " ".join(str(text or "").strip().lower().split())

    def _short_digest(obj: Any, max_chars: int = 256) -> str:
        import json
        raw = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
        return raw[:max_chars]

    stable_parts: Dict[str, Any] = {
        "input": _normalize(user_input),
    }
    if isinstance(context, dict):
        for k in ("system_prompt", "recent_messages", "user_text"):
            v = context.get(k)
            if v:
                stable_parts[k] = _short_digest(v)

    raw = json.dumps([
        ("input", stable_parts.get("input", "")),
        ("context_digest", _short_digest(stable_parts)),
    ], sort_keys=True, ensure_ascii=False)

    return hashlib.sha256(raw.encode()).hexdigest()


def get(key: str) -> Optional[Dict[str, Any]]:
    """Get cached response by key from SQLite. Returns None on miss or expired TTL."""
    if not _proxy_cache_enabled():
        return None
    try:
        conn = _ensure_sqlite()
        row = conn.execute(
            "SELECT response, ts, ttl FROM llm_cache WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        response_str, stored_ts, ttl = row
        now = time.time()
        if ttl > 0 and (now - stored_ts) > ttl:
            conn.execute("DELETE FROM llm_cache WHERE key = ?", (key,))
            conn.commit()
            return None
        return json.loads(response_str)
    except Exception as exc:
        logger.exception("LLM cache get failed: %s", exc)
        return None


def set(key: str, response: Dict[str, Any], ttl: Optional[float] = None) -> None:
    """Store response in SQLite cache with optional TTL."""
    if not _proxy_cache_enabled():
        return
    try:
        conn = _ensure_sqlite()
        t = ttl if ttl is not None else _default_ttl()
        conn.execute(
            "INSERT OR REPLACE INTO llm_cache (key, response, ts, ttl) VALUES (?, ?, ?, ?)",
            (key, json.dumps(response, ensure_ascii=False, default=str), time.time(), t),
        )
        conn.commit()
        _purge_old_entries(conn)
    except Exception as exc:
        logger.exception("LLM cache set failed: %s", exc)


def invalidate_on_reset(reason: str = "") -> int:
    """Invalidate cache entries on context reset. Returns count of removed entries."""
    try:
        conn = _ensure_sqlite()
        cursor = conn.execute("DELETE FROM llm_cache")
        count = cursor.rowcount
        conn.commit()
        global _cache_hits, _cache_misses, _reasoning_cache_hits, _reasoning_cache_misses
        _cache_hits = 0
        _cache_misses = 0
        _reasoning_cache_hits = 0
        _reasoning_cache_misses = 0
        _CACHE.clear()
        _REASONING_CACHE.clear()
        return count
    except Exception as exc:
        logger.exception("LLM cache invalidate_on_reset failed: %s", exc)
        return 0


def _purge_old_entries(conn: sqlite3.Connection, max_entries: int = 10000) -> None:
    try:
        conn.execute(
            "DELETE FROM llm_cache WHERE ts < ?",
            (time.time() - _default_ttl(),),
        )
        count = conn.execute("SELECT COUNT(*) FROM llm_cache").fetchone()
        if count and count[0] > max_entries:
            conn.execute(
                "DELETE FROM llm_cache WHERE key IN ("
                "  SELECT key FROM llm_cache ORDER BY ts ASC LIMIT ?"
                ")",
                (count[0] - max_entries,),
            )
        conn.commit()
    except Exception as exc:
        logger.exception("LLM cache purge_old_entries failed: %s", exc)

_CACHE: OrderedDict[str, Dict[str, Any]] = OrderedDict()
_REASONING_CACHE: OrderedDict[str, Dict[str, Any]] = OrderedDict()
_cache_hits: int = 0
_cache_misses: int = 0
_reasoning_cache_hits: int = 0
_reasoning_cache_misses: int = 0
_cache_lock = threading.Lock()


def _cache_key(
    *,
    model: str,
    system_prompt: str,
    user_input: str,
    bound_object: Optional[Any] = None,
    tools_signature: str = "",
    subject: str = "",
    memory_state: str = "",
) -> str:
    raw = json.dumps([
        ("model", str(model or "")),
        ("system", str(system_prompt or "")[:256]),
        ("input", _hash_input(user_input or "")),
        ("tools", str(tools_signature or "")[:128]),
        ("subject", str(subject or "")[:128]),
        ("memory_state", str(memory_state or "")[:128]),
    ], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _hash_input(text: str) -> str:
    """Hash the full user input to avoid cache collisions on different questions."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def llm_cache_lookup(
    *,
    model: str,
    system_prompt: str,
    user_input: str,
    bound_object: Optional[Any] = None,
    tools_signature: str = "",
    subject: str = "",
    memory_state: str = "",
) -> Optional[Dict[str, Any]]:
    """Look up a cached LLM response. Returns None on miss or if cache is disabled."""
    if not cache_enabled():
        return None
    key = _cache_key(
        model=model,
        system_prompt=system_prompt,
        user_input=user_input,
        bound_object=bound_object,
        tools_signature=tools_signature,
        subject=subject,
        memory_state=memory_state,
    )
    with _cache_lock:
        entry = _CACHE.get(key)
        if not entry:
            global _cache_misses
            _cache_misses += 1
            return None
        ttl = cache_ttl_seconds()
        if ttl > 0 and (time.time() - float(entry.get("ts") or 0)) > ttl:
            del _CACHE[key]
            _cache_misses += 1
            return None
        global _cache_hits
        _cache_hits += 1
    return dict(entry["result"])


def llm_cache_store(
    *,
    model: str,
    system_prompt: str,
    user_input: str,
    bound_object: Optional[Any] = None,
    tools_signature: str = "",
    subject: str = "",
    memory_state: str = "",
    result: Dict[str, Any],
) -> None:
    """Store an LLM response in the cache."""
    if not cache_enabled():
        return
    # Don't cache tool-call responses (they have side effects)
    if should_skip_cache(result):
        return
    key = _cache_key(
        model=model,
        system_prompt=system_prompt,
        user_input=user_input,
        bound_object=bound_object,
        tools_signature=tools_signature,
        subject=subject,
        memory_state=memory_state,
    )
    with _cache_lock:
        _CACHE[key] = {"ts": time.time(), "result": dict(result)}
        # Enforce max entries
        max_entries = cache_max_entries()
        if max_entries > 0:
            while len(_CACHE) > max_entries:
                _CACHE.popitem(last=False)


def reasoning_cache_lookup(
    *,
    model: str,
    system_prompt: str,
    user_input: str,
    subject: str = "",
    memory_state: str = "",
    tools_signature: str = "",
) -> Optional[Dict[str, Any]]:
    """Look up a cached reasoning result. Returns None on miss or if cache is disabled."""
    if not cache_enabled():
        return None
    key = _cache_key(
        model=model,
        system_prompt=system_prompt,
        user_input=user_input,
        subject=subject,
        memory_state=memory_state,
        tools_signature=tools_signature,
    )
    with _cache_lock:
        entry = _REASONING_CACHE.get(key)
        if not entry:
            global _reasoning_cache_misses
            _reasoning_cache_misses += 1
            return None
        ttl = cache_ttl_seconds()
        if ttl > 0 and (time.time() - float(entry.get("ts") or 0)) > ttl:
            del _REASONING_CACHE[key]
            _reasoning_cache_misses += 1
            return None
        global _reasoning_cache_hits
        _reasoning_cache_hits += 1
    return {
        "content": entry.get("content"),
        "tool_calls": entry.get("tool_calls"),
        "reasoning_decision": entry.get("reasoning_decision"),
    }


def reasoning_cache_store(
    *,
    model: str,
    system_prompt: str,
    user_input: str,
    subject: str = "",
    memory_state: str = "",
    tools_signature: str = "",
    content: Optional[str] = None,
    tool_calls: Optional[list] = None,
    reasoning_decision: Optional[Dict[str, Any]] = None,
) -> None:
    """Store a reasoning result in the cache."""
    if not cache_enabled():
        return
    key = _cache_key(
        model=model,
        system_prompt=system_prompt,
        user_input=user_input,
        subject=subject,
        memory_state=memory_state,
        tools_signature=tools_signature,
    )
    with _cache_lock:
        _REASONING_CACHE[key] = {
            "ts": time.time(),
            "content": content,
            "tool_calls": tool_calls,
            "reasoning_decision": reasoning_decision,
        }
        max_entries = cache_max_entries()
        if max_entries > 0:
            while len(_REASONING_CACHE) > max_entries:
                _REASONING_CACHE.popitem(last=False)


def should_skip_cache(result: Dict[str, Any]) -> bool:
    """Don't cache when the response contains a tool call — those have dependencies."""
    if not isinstance(result, dict):
        return True
    content = str(result.get("content") or "")
    if "TOOL_CALL:" in content:
        return True
    if result.get("error"):
        return True
    return False


def llm_cache_stats() -> Dict[str, Any]:
    with _cache_lock:
        return {
            "enabled": cache_enabled(),
            "entries": len(_CACHE),
            "reasoning_entries": len(_REASONING_CACHE),
            "max_entries": cache_max_entries(),
            "ttl_seconds": cache_ttl_seconds(),
            "hits": _cache_hits,
            "misses": _cache_misses,
            "reasoning_hits": _reasoning_cache_hits,
            "reasoning_misses": _reasoning_cache_misses,
        }


def llm_cache_clear() -> None:
    with _cache_lock:
        _CACHE.clear()
        _REASONING_CACHE.clear()
        global _cache_hits, _cache_misses, _reasoning_cache_hits, _reasoning_cache_misses
        _cache_hits = 0
        _cache_misses = 0
        _reasoning_cache_hits = 0
        _reasoning_cache_misses = 0
    try:
        conn = _ensure_sqlite()
        conn.execute("DELETE FROM llm_cache")
        conn.commit()
    except Exception as exc:
        logger.exception("LLM cache clear sqlite failed: %s", exc)
