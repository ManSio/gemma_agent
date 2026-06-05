"""
Кэш готового LLM-дайджеста новостей: не гонять модель на каждый «какие новости».

Слой 1 — fingerprint заголовков RSS/поиска (есть ли новые сюжеты).
Слой 2 — текст дайджеста после LLM (narrative / summaries).

При том же запросе в пределах TTL: если заголовки не изменились — отдаём кэш.
Если появились новые заголовки — только тогда снова LLM.

Env: NEWS_DIGEST_CACHE_ENABLED, NEWS_DIGEST_CACHE_TTL_SEC (по умолчанию 3600).
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_store: Dict[str, Dict[str, Any]] = {}


def _truthy(name: str, *, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    if raw in {"0", "false", "no", "off"}:
        return False
    return raw in {"1", "true", "yes", "on"}


def enabled() -> bool:
    return _truthy("NEWS_DIGEST_CACHE_ENABLED", default=True)


def ttl_sec() -> int:
    try:
        return max(300, min(7200, int((os.getenv("NEWS_DIGEST_CACHE_TTL_SEC") or "3600").strip())))
    except ValueError:
        return 3600


def max_entries() -> int:
    try:
        return max(16, min(500, int((os.getenv("NEWS_DIGEST_CACHE_MAX_ENTRIES") or "64").strip())))
    except ValueError:
        return 64


def _norm_query(query: str) -> str:
    t = re.sub(r"\s+", " ", (query or "").strip().lower())
    if not t:
        return "world"
    if re.search(r"(?i)мир|world|международ", t):
        return "world"
    if re.search(r"(?i)новост", t):
        return "news"
    return t[:120]


def cache_key(
    *,
    user_query: str,
    country: str = "",
    world_feed: bool = False,
    expanded: bool = False,
    digest_format: str = "narrative",
    narrative_style: str = "per_item",
) -> str:
    parts = [
        _norm_query(user_query),
        (country or "").strip().upper()[:8],
        "1" if world_feed else "0",
        "exp" if expanded else "brief",
        (digest_format or "narrative").strip().lower()[:12],
        (narrative_style or "per_item").strip().lower()[:12],
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def items_fingerprint(displayed: List[Dict[str, Any]]) -> str:
    """Стабильный отпечаток топ-заголовков — новый сюжет → другой hash."""
    lines: List[str] = []
    for row in displayed[:10]:
        if not isinstance(row, dict):
            continue
        title = re.sub(r"\s+", " ", str(row.get("title") or "").strip().lower())
        if len(title) >= 8:
            lines.append(title[:200])
    if not lines:
        return ""
    blob = "\n".join(lines)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:20]


def _evict_expired(now: float) -> None:
    dead = [k for k, v in _store.items() if float(v.get("expires", 0)) < now]
    for k in dead:
        _store.pop(k, None)
    if len(_store) <= max_entries():
        return
    ordered = sorted(_store.items(), key=lambda kv: float(kv[1].get("stored_at", 0)))
    for k, _ in ordered[: max(1, len(_store) - max_entries())]:
        _store.pop(k, None)


def get_cached_compose(
    key: str,
    fingerprint: str,
) -> Optional[str]:
    """Готовый текст дайджеста, если TTL не вышел и заголовки те же."""
    if not enabled() or not key or not fingerprint:
        return None
    now = time.time()
    with _lock:
        _evict_expired(now)
        row = _store.get(key)
        if not isinstance(row, dict):
            return None
        if float(row.get("expires", 0)) < now:
            _store.pop(key, None)
            return None
        if str(row.get("fingerprint") or "") != fingerprint:
            return None
        text = str(row.get("reply") or "").strip()
        return text if len(text) >= 80 else None


def put_cached_compose(
    key: str,
    fingerprint: str,
    reply: str,
) -> None:
    if not enabled() or not key or not fingerprint:
        return
    body = (reply or "").strip()
    if len(body) < 80:
        return
    now = time.time()
    with _lock:
        _evict_expired(now)
        _store[key] = {
            "fingerprint": fingerprint,
            "reply": body,
            "stored_at": now,
            "expires": now + ttl_sec(),
            "hour_bucket": int(now // 3600),
        }


def cache_status(key: str) -> Dict[str, Any]:
    """Для отладки / метрик."""
    now = time.time()
    with _lock:
        row = _store.get(key)
        if not isinstance(row, dict):
            return {"hit": False}
        return {
            "hit": float(row.get("expires", 0)) >= now,
            "fingerprint": row.get("fingerprint"),
            "age_sec": int(now - float(row.get("stored_at", now))),
            "hour_bucket": row.get("hour_bucket"),
        }
