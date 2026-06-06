"""
Кэш текстов нормативных актов (URL → JSON) и простой индекс по ключевым словам.
LawSearch-модуль в public-сборке отключён; кэш используется DocumentCorpus и ручным ingest.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[\w\-]+", re.UNICODE)


def law_cache_dir() -> Path:
    return Path(os.getenv("LAW_ACT_CACHE_DIR", os.path.join("data", "law_act_cache")))


def law_cache_ttl_sec() -> int:
    try:
        return max(60, int(os.getenv("LAW_ACT_CACHE_TTL_SEC", str(7 * 24 * 3600))))
    except ValueError:
        return 7 * 24 * 3600


def law_index_max_tokens_per_key() -> int:
    try:
        return max(10, int(os.getenv("LAW_INDEX_MAX_KEYS_PER_TOKEN", "48")))
    except ValueError:
        return 48


def _norm_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    p = urlparse(u)
    if not p.scheme or not p.netloc:
        return u
    path = p.path or "/"
    return f"{p.scheme}://{p.netloc.lower()}{path}" + (f"?{p.query}" if p.query else "")


def cache_key_for_url(url: str) -> str:
    n = _norm_url(url)
    return hashlib.sha256(n.encode("utf-8")).hexdigest()[:40]


def _entry_path(key: str) -> Path:
    d = law_cache_dir() / "entries"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{key}.json"


def _index_path() -> Path:
    law_cache_dir().mkdir(parents=True, exist_ok=True)
    return law_cache_dir() / "keyword_index.json"


def _tokenize(text: str) -> Set[str]:
    low = (text or "").lower()
    return {t for t in _TOKEN_RE.findall(low) if len(t) >= 3}


def cache_get(url: str) -> Optional[Dict[str, Any]]:
    key = cache_key_for_url(url)
    path = _entry_path(key)
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        data["_cache_key"] = key
        return data
    except Exception as e:
        logger.debug("[law_cache] read %s: %s", path, e)
        return None


def cache_expired(entry: Dict[str, Any]) -> bool:
    try:
        fetched = float(entry.get("fetched_at_unix") or 0)
    except (TypeError, ValueError):
        fetched = 0
    if fetched <= 0:
        return True
    return (time.time() - fetched) > law_cache_ttl_sec()


def cache_put(
    url: str,
    *,
    title: str,
    text: str,
    source: str = "law_search",
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    key = cache_key_for_url(url)
    path = _entry_path(key)
    payload: Dict[str, Any] = {
        "url": _norm_url(url),
        "title": (title or "")[:500],
        "text": text or "",
        "fetched_at_unix": time.time(),
        "source": source[:64],
    }
    if extra:
        payload["meta"] = extra
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        index_add(key, payload["url"], payload["title"], payload["text"])
        try:
            from core.document_corpus_store import corpus_enabled, register_law_act_from_cache

            if corpus_enabled():
                register_law_act_from_cache(
                    cache_key=key,
                    url=payload["url"],
                    title=payload["title"],
                    text=payload["text"],
                )
        except Exception as e:
            logger.debug("[law_cache] document_corpus register: %s", e)
    except Exception as e:
        logger.warning("[law_cache] write %s: %s", path, e)
    return key


def _load_index() -> Dict[str, List[str]]:
    p = _index_path()
    if not p.is_file():
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, list)}
    except Exception as e:
        logger.debug("[law_cache] index read: %s", e)
    return {}


def _save_index(idx: Dict[str, List[str]]) -> None:
    try:
        with open(_index_path(), "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False)
    except Exception as e:
        logger.warning("[law_cache] index write: %s", e)


def index_add(cache_key: str, url: str, title: str, text: str) -> None:
    blob = f"{title}\n{(text or '')[:200000]}"
    tokens = _tokenize(blob)
    if len(tokens) > 4000:
        tokens = set(sorted(tokens)[:4000])
    if not tokens:
        return
    idx = _load_index()
    cap = law_index_max_tokens_per_key()
    for tok in tokens:
        lst = list(idx.get(tok) or [])
        if cache_key in lst:
            continue
        lst.insert(0, cache_key)
        idx[tok] = lst[:cap]
    _save_index(idx)


def keyword_search(query: str, *, limit: int = 8) -> List[Dict[str, Any]]:
    q = (query or "").strip().lower()
    if len(q) < 2:
        return []
    tokens = [t for t in _tokenize(q) if len(t) >= 3]
    if not tokens:
        tokens = [q] if len(q) >= 3 else []
    if not tokens:
        return []
    idx = _load_index()
    scores: Dict[str, int] = {}
    for tok in tokens:
        for key in idx.get(tok) or []:
            scores[key] = scores.get(key, 0) + 1
    ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0]))[: max(1, limit * 2)]
    out: List[Dict[str, Any]] = []
    for key, score in ranked:
        path = law_cache_dir() / "entries" / f"{key}.json"
        if not path.is_file():
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                row = json.load(f)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "")
        snippet = text[:420].replace("\n", " ").strip()
        if len(text) > 420:
            snippet += "…"
        out.append(
            {
                "url": row.get("url"),
                "title": row.get("title"),
                "snippet": snippet,
                "score": score,
            }
        )
        if len(out) >= limit:
            break
    return out


def cache_stats() -> Dict[str, Any]:
    d = law_cache_dir() / "entries"
    n = 0
    if d.is_dir():
        n = sum(1 for x in d.glob("*.json"))
    idx = _load_index()
    return {
        "entries": n,
        "index_tokens": len(idx),
        "ttl_sec": law_cache_ttl_sec(),
        "dir": str(law_cache_dir()),
    }
