"""
Универсальный поиск для агента: один инструмент, несколько бэкендов.

Порядок задаётся env (см. UNIVERSAL_SEARCH_FREE_ONLY, UNIVERSAL_SEARCH_LOCAL_FIRST).

Провайдеры:
- Локально / свой хост: SearXNG (SEARXNG_INSTANCE_URL), JSON search API
- Платные (опционально): Tavily, Brave
- Бесплатно без ключей: Wikipedia → DuckDuckGo Instant Answer → при пустом ответе HTML-выдача DDG → Google News RSS

Не заменяет UrlFetch: если у пользователя есть конкретный https URL — лучше fetch_page.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import aiohttp

from modules.external_apis.service import ExternalAPIService

logger = logging.getLogger(__name__)

_search_timeout = max(5.0, float(os.getenv("UNIVERSAL_SEARCH_TIMEOUT_SEC", "20")))
DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=_search_timeout)
UA = os.getenv(
    "HTTP_USER_AGENT",
    "GemmaAgent/1.0 (+https://github.com/ManSio/gemma_agent; universal search)",
)


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _clip(s: str, max_len: int) -> str:
    t = (s or "").strip()
    if len(t) <= max_len:
        return t
    return t[: max_len - 1] + "…"


async def _post_json(url: str, body: Dict[str, Any]) -> Tuple[int, Any]:
    headers = {"User-Agent": UA, "Accept": "application/json", "Content-Type": "application/json"}
    async with aiohttp.ClientSession(timeout=DEFAULT_TIMEOUT) as session:
        async with session.post(url, headers=headers, data=json.dumps(body)) as resp:
            text = await resp.text()
            if resp.status != 200:
                return resp.status, None
            try:
                return resp.status, json.loads(text)
            except json.JSONDecodeError:
                return resp.status, None


async def _get_json(url: str, headers: Optional[Dict[str, str]] = None) -> Tuple[int, Any]:
    h = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    async with aiohttp.ClientSession(timeout=DEFAULT_TIMEOUT) as session:
        async with session.get(url, headers=h) as resp:
            text = await resp.text()
            if resp.status != 200:
                return resp.status, None
            try:
                return resp.status, json.loads(text)
            except json.JSONDecodeError:
                return resp.status, None


async def _try_tavily(query: str) -> Optional[Dict[str, Any]]:
    key = (os.getenv("TAVILY_API_KEY") or "").strip()
    if not key or not _truthy("TAVILY_SEARCH_ENABLED", True):
        return None
    try:
        n = int(os.getenv("TAVILY_MAX_RESULTS", "5"))
    except ValueError:
        n = 5
    n = max(3, min(n, 10))
    depth = (os.getenv("TAVILY_SEARCH_DEPTH") or "basic").strip() or "basic"
    body = {
        "api_key": key,
        "query": _clip(query, 400),
        "search_depth": depth if depth in {"basic", "advanced"} else "basic",
        "include_answer": True,
        "max_results": n,
    }
    status, data = await _post_json("https://api.tavily.com/search", body)
    if status != 200 or not isinstance(data, dict):
        logger.debug("tavily search http=%s", status)
        return None
    answer = (data.get("answer") or "").strip()
    raw_results = data.get("results") if isinstance(data.get("results"), list) else []
    results: List[Dict[str, str]] = []
    for row in raw_results[:n]:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        url = str(row.get("url") or "").strip()
        content = str(row.get("content") or row.get("snippet") or "").strip()
        if title or content:
            results.append({"title": title, "url": url, "snippet": _clip(content, 500)})
    parts: List[str] = []
    if answer:
        parts.append(answer)
    for r in results[:4]:
        line = r["title"] + (f" — {r['snippet']}" if r.get("snippet") else "")
        if r.get("url"):
            line += f" ({r['url']})"
        if line.strip():
            parts.append(line.strip())
    if not parts:
        return None
    summary = _clip("\n".join(parts), int(os.getenv("UNIVERSAL_SEARCH_MAX_SUMMARY_CHARS", "6000")))
    return {
        "ok": True,
        "source": "tavily",
        "query": query,
        "summary": summary,
        "results": results,
        "hint": "Сводка из Tavily; при необходимости уточни по конкретной ссылке через UrlFetch.fetch_page.",
    }


async def _try_searx(query: str, *, categories: str = "general") -> Optional[Dict[str, Any]]:
    """SearXNG /search?format=json — поднимите свой инстанс или используйте доверенный."""
    base = (os.getenv("SEARXNG_INSTANCE_URL") or os.getenv("UNIVERSAL_SEARCH_SEARX_URL") or "").strip().rstrip("/")
    if not base or not _truthy("SEARXNG_ENABLED", True):
        return None
    try:
        n = int(os.getenv("SEARXNG_MAX_RESULTS", "8"))
    except ValueError:
        n = 8
    n = max(2, min(n, 20))
    cats = (categories or "general").strip() or "general"
    params = urlencode({"q": _clip(query, 500), "format": "json", "categories": cats})
    url = f"{base}/search?{params}"
    status, data = await _get_json(url)
    if status != 200 or not isinstance(data, dict):
        logger.debug("searx search http=%s", status)
        return None
    raw_results = data.get("results") if isinstance(data.get("results"), list) else []
    results: List[Dict[str, str]] = []
    parts: List[str] = []
    for row in raw_results[:n]:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        url_u = str(row.get("url") or "").strip()
        content = str(row.get("content") or "").strip()
        if title or content:
            results.append({"title": title, "url": url_u, "snippet": _clip(content, 600)})
            parts.append(_clip(f"{title}: {content}" + (f" ({url_u})" if url_u else ""), 800))
    if not parts:
        return None
    summary = _clip("\n".join(parts), int(os.getenv("UNIVERSAL_SEARCH_MAX_SUMMARY_CHARS", "6000")))
    return {
        "ok": True,
        "source": "searxng",
        "query": query,
        "summary": summary,
        "results": results,
        "hint": "Выдача SearXNG (локально или свой сервер). Полный текст страницы — UrlFetch.fetch_page по url.",
    }


async def _try_brave(query: str) -> Optional[Dict[str, Any]]:
    key = (os.getenv("BRAVE_SEARCH_API_KEY") or "").strip()
    if not key or not _truthy("BRAVE_SEARCH_ENABLED", True):
        return None
    try:
        count = int(os.getenv("BRAVE_SEARCH_COUNT", "5"))
    except ValueError:
        count = 5
    count = max(1, min(count, 10))
    qs = urlencode({"q": query, "count": str(count)})
    url = f"https://api.search.brave.com/res/v1/web/search?{qs}"
    status, data = await _get_json(
        url,
        headers={
            "X-Subscription-Token": key,
            "Accept": "application/json",
        },
    )
    if status != 200 or not isinstance(data, dict):
        logger.debug("brave search http=%s", status)
        return None
    web = data.get("web") or {}
    raw_results = web.get("results") if isinstance(web.get("results"), list) else []
    results: List[Dict[str, str]] = []
    parts: List[str] = []
    for row in raw_results[:count]:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        url_u = str(row.get("url") or "").strip()
        desc = str(row.get("description") or "").strip()
        if title or desc:
            results.append({"title": title, "url": url_u, "snippet": _clip(desc, 500)})
            parts.append(
                _clip(f"{title}: {desc}" + (f" {url_u}" if url_u else ""), 700)
            )
    if not parts:
        return None
    summary = _clip("\n".join(parts), int(os.getenv("UNIVERSAL_SEARCH_MAX_SUMMARY_CHARS", "6000")))
    return {
        "ok": True,
        "source": "brave",
        "query": query,
        "summary": summary,
        "results": results,
        "hint": "Выдача Brave Search; для полного текста страницы используй UrlFetch.fetch_page по url.",
    }


def _from_lookup_fallback(
    query: str, pack: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    src = pack.get("source")
    data = pack.get("data")
    if not isinstance(data, dict):
        return None
    if not data.get("configured"):
        return None
    summary = (data.get("summary") or "").strip()
    if not summary:
        return None
    return {
        "ok": True,
        "source": str(src or "aggregated"),
        "query": query,
        "summary": _clip(summary, int(os.getenv("UNIVERSAL_SEARCH_MAX_SUMMARY_CHARS", "6000"))),
        "results": [],
        "hint": f"Источник: {src} (без платного API). Для проверки по первоисточнику — UrlFetch, если есть URL.",
    }


class UniversalSearchModule:
    """Инструмент UniversalSearch.search — единая точка веб-справки для мозга."""

    async def search(self, query: str, country: str = "", user_id: str = "", **kwargs: Any) -> Dict[str, Any]:
        searx_categories = str(kwargs.get("searx_categories") or kwargs.get("categories") or "").strip()
        if not searx_categories and _truthy("NEWS_SEARCH_SEARX_NEWS_CATEGORY", default=False):
            q_low = (query or "").lower()
            if "новост" in q_low or "news" in q_low:
                searx_categories = "news"
        if not _truthy("UNIVERSAL_SEARCH_ENABLED", True):
            return {"ok": False, "error": "universal search disabled (UNIVERSAL_SEARCH_ENABLED=false)"}
        q = (query or "").strip()
        if not q:
            return {"ok": False, "error": "query required", "hint": "Передай короткую строку запроса на естественном языке."}

        free_only = _truthy("UNIVERSAL_SEARCH_FREE_ONLY", False)
        local_first = _truthy("UNIVERSAL_SEARCH_LOCAL_FIRST", False)

        async def _searx_once() -> Optional[Dict[str, Any]]:
            return await _try_searx(q, categories=searx_categories or "general")

        if free_only:
            s = await _searx_once()
            if s:
                return s
        else:
            if local_first:
                s = await _searx_once()
                if s:
                    return s
            t = await _try_tavily(q)
            if t:
                return t
            b = await _try_brave(q)
            if b:
                return b
            if not local_first:
                s = await _searx_once()
                if s:
                    return s

        try:
            svc = ExternalAPIService()
            pack = await svc.lookup_or_fallback(q, country=(country or "").strip())
            agg = _from_lookup_fallback(q, pack)
            if agg:
                return agg
        except Exception as e:
            logger.warning("universal search fallback failed: %s", e)
        return {
            "ok": False,
            "error": "no results from configured providers",
            "query": q,
            "hint": (
                "Типичная причина пустой выдачи: DuckDuckGo с IP VPS отдаёт антибот вместо выдачи. "
                "Без западных API (Tavily/Brave и т.п.): поднимите SearXNG и задайте SEARXNG_INSTANCE_URL — на инстансе выбираете движки "
                "(в т.ч. доступные в вашем регионе). Варианты: UNIVERSAL_SEARCH_LOCAL_FIRST=true (сначала SearXNG, потом остальное) "
                "или UNIVERSAL_SEARCH_FREE_ONLY=true (только SearXNG, без DDG). Готовый текст по ссылке — UrlFetch.fetch_page."
            ),
        }
