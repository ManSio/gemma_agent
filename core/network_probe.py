"""
Дополнительные HTTP-замеры задержки (без ICMP): реальные round-trip до публичных endpoint.
Для интерпретации: большое число мс здесь ≠ «плохой LLM»; длинная генерация токенов — отдельная метрика.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse

import aiohttp

logger = logging.getLogger(__name__)


def _probe_timeout_sec() -> float:
    try:
        return float(os.getenv("NETWORK_PROBE_TIMEOUT_SEC", "12"))
    except ValueError:
        return 12.0


def _truthy_env(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _default_probe_headers() -> Dict[str, str]:
    ua = (os.getenv("HTTP_USER_AGENT") or "").strip()
    if not ua:
        ua = "GemmaAgent/1.0 (+https://github.com/gemma-agent/gemma-agent; connectivity-probe)"
    return {"User-Agent": ua}


def collect_plugin_http_probe_specs() -> List[Dict[str, Any]]:
    """
    Лёгкие GET к бэкендам, которые включаются через env (SearXNG, Qdrant, Tavily, Brave, …).
    Не дублирует Telegram/OpenRouter/Mem0 — они проверяются в connectivity_check.
    """
    specs: List[Dict[str, Any]] = []

    base_sx = (
        (os.getenv("SEARXNG_INSTANCE_URL") or os.getenv("UNIVERSAL_SEARCH_SEARX_URL") or "")
        .strip()
        .rstrip("/")
    )
    if base_sx and _truthy_env("SEARXNG_ENABLED", True):
        specs.append(
            {
                "name": "searxng_search",
                "url": f"{base_sx}/search?q={quote('connectivity')}&format=json",
                "max_bytes": 98304,
            }
        )

    qd = (os.getenv("QDRANT_URL") or "").strip().rstrip("/")
    if qd:
        h: Dict[str, str] = dict(_default_probe_headers())
        qk = (os.getenv("QDRANT_API_KEY") or "").strip()
        if qk:
            h["api-key"] = qk
        specs.append({"name": "qdrant_collections", "url": f"{qd}/collections", "headers": h})

    if (os.getenv("TAVILY_API_KEY") or "").strip() and _truthy_env("TAVILY_SEARCH_ENABLED", True):
        specs.append(
            {
                "name": "tavily_api_host",
                "url": "https://api.tavily.com/",
                "max_bytes": 4096,
            }
        )

    brave_key = (os.getenv("BRAVE_SEARCH_API_KEY") or "").strip()
    if brave_key and _truthy_env("BRAVE_SEARCH_ENABLED", True):
        h2 = dict(_default_probe_headers())
        h2["X-Subscription-Token"] = brave_key
        h2["Accept"] = "application/json"
        specs.append(
            {
                "name": "brave_web_search",
                "url": "https://api.search.brave.com/res/v1/web/search?q=connectivity&count=1",
                "headers": h2,
                "max_bytes": 65536,
            }
        )

    stt = (os.getenv("VOICE_STT_API_URL") or "").strip()
    if stt:
        try:
            p = urlparse(stt)
            if p.scheme and p.netloc:
                origin = f"{p.scheme}://{p.netloc}/"
                specs.append({"name": "voice_stt_origin", "url": origin, "max_bytes": 8192})
        except Exception as e:
            logger.debug('%s optional failed: %s', 'network_probe', e, exc_info=True)
    mirror = (os.getenv("URL_FETCH_MIRROR_BASE") or "").strip()
    if mirror:
        m = mirror.rstrip("/") + "/"
        specs.append({"name": "url_fetch_mirror", "url": m, "max_bytes": 8192})

    raw_extra = (os.getenv("CONNECTIVITY_EXTRA_HTTP_PROBES") or "").strip()
    if raw_extra:
        for part in raw_extra.split(","):
            chunk = part.strip()
            if "|" not in chunk:
                continue
            n, u = chunk.split("|", 1)
            n, u = n.strip(), u.strip()
            if n and u:
                specs.append({"name": n, "url": u, "max_bytes": 8192})

    return specs


async def http_get_roundtrip(
    url: str,
    *,
    name: str,
    timeout_sec: Optional[float] = None,
    max_bytes: int = 65536,
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """GET с полным чтением до max_bytes; измеряет wall time до конца тела."""
    to = max(3.0, min(timeout_sec or _probe_timeout_sec(), 60.0))
    t0 = time.perf_counter()
    hdrs = dict(_default_probe_headers())
    if headers:
        hdrs.update(headers)
    try:
        timeout = aiohttp.ClientTimeout(total=to)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, allow_redirects=True, headers=hdrs) as resp:
                raw = await resp.content.read(max_bytes + 1)
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                truncated = len(raw) > max_bytes
                return {
                    "name": name,
                    "url": url,
                    "ok": resp.status < 500,
                    "http_status": resp.status,
                    "roundtrip_ms": round(elapsed_ms, 2),
                    "bytes_read": min(len(raw), max_bytes),
                    "truncated": truncated,
                }
    except asyncio.TimeoutError:
        return {
            "name": name,
            "url": url,
            "ok": False,
            "http_status": None,
            "roundtrip_ms": None,
            "error": "timeout",
        }
    except aiohttp.ClientError as e:
        return {
            "name": name,
            "url": url,
            "ok": False,
            "http_status": None,
            "roundtrip_ms": None,
            "error": str(e),
        }


DEFAULT_PROBES: List[Dict[str, str]] = [
    {"name": "openrouter_models", "url": "https://openrouter.ai/api/v1/models"},
    {"name": "telegram_api_host", "url": "https://api.telegram.org/"},
    {"name": "cloudflare_trace", "url": "https://www.cloudflare.com/cdn-cgi/trace"},
]


async def run_plugin_http_probes(
    *,
    specs: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Параллельные GET только к настроенным облачным/самохост бэкендам плагинов."""
    use = list(specs) if specs is not None else collect_plugin_http_probe_specs()
    if not use:
        return {"label": "plugin_http_probes", "timeout_sec": _probe_timeout_sec(), "results": []}
    tasks = [
        http_get_roundtrip(
            p["url"],
            name=p["name"],
            max_bytes=int(p.get("max_bytes") or 8192),
            headers=p.get("headers") if isinstance(p.get("headers"), dict) else None,
        )
        for p in use
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out: List[Dict[str, Any]] = []
    for r in results:
        if isinstance(r, Exception):
            logger.debug("plugin_http_probe task error: %s", r)
            out.append({"ok": False, "error": str(r)})
        else:
            out.append(r)
    return {"label": "plugin_http_probes", "timeout_sec": _probe_timeout_sec(), "results": out}


async def run_http_latency_probes(
    *,
    extra_urls: Optional[List[Dict[str, str]]] = None,
    include_plugin_endpoints: bool = True,
) -> Dict[str, Any]:
    """Параллельные лёгкие GET — снимок сети до ключевых хостов."""
    probes: List[Any] = list(DEFAULT_PROBES)
    if extra_urls:
        probes.extend(extra_urls)
    if include_plugin_endpoints:
        probes.extend(collect_plugin_http_probe_specs())
    tasks = [
        http_get_roundtrip(
            p["url"],
            name=p["name"],
            max_bytes=int(p.get("max_bytes", 8192)) if isinstance(p, dict) and "max_bytes" in p else (
                98304 if "models" in p["url"] else 8192
            ),
            headers=p.get("headers") if isinstance(p, dict) and isinstance(p.get("headers"), dict) else None,
        )
        for p in probes
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out: List[Dict[str, Any]] = []
    for r in results:
        if isinstance(r, Exception):
            logger.debug("network_probe task error: %s", r)
            out.append({"ok": False, "error": str(r)})
        else:
            out.append(r)
    return {
        "label": "http_latency_probes",
        "timeout_sec": _probe_timeout_sec(),
        "results": out,
    }
