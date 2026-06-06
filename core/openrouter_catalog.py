"""
Каталог моделей OpenRouter (публичный GET /api/v1/models) + нормализация цен.
Кэш в памяти: сброс через invalidate_openrouter_models_cache() или force_refresh.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

logger = logging.getLogger(__name__)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

_cache_lock = asyncio.Lock()
_cache: Optional[Tuple[float, List[Dict[str, Any]]]] = None


def invalidate_openrouter_models_cache() -> None:
    """Следующий fetch снова пойдёт в API."""
    global _cache
    _cache = None


def _cache_ttl_sec() -> float:
    try:
        return float(os.getenv("OPENROUTER_MODELS_CACHE_SEC", "600"))
    except ValueError:
        return 600.0


def _parse_price_per_token_usd(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    try:
        return float(str(raw).strip())
    except ValueError:
        return None


def normalize_model_record(m: Dict[str, Any]) -> Dict[str, Any]:
    """
    Цены в API — USD за 1 токен (строки). Дублируем как USD за 1M токенов для читаемости.
    """
    pricing = m.get("pricing") if isinstance(m.get("pricing"), dict) else {}
    p_tok = _parse_price_per_token_usd(pricing.get("prompt"))
    c_tok = _parse_price_per_token_usd(pricing.get("completion"))
    freeish = (p_tok == 0 or p_tok is None) and (c_tok == 0 or c_tok is None)
    return {
        "id": m.get("id"),
        "name": m.get("name"),
        "context_length": m.get("context_length"),
        "prompt_usd_per_1m": round(p_tok * 1_000_000, 6) if p_tok is not None else None,
        "completion_usd_per_1m": round(c_tok * 1_000_000, 6) if c_tok is not None else None,
        "likely_free_route": bool(freeish),
        "canonical_slug": m.get("canonical_slug"),
    }


async def fetch_openrouter_models_raw(*, timeout_sec: float = 45.0) -> List[Dict[str, Any]]:
    timeout = aiohttp.ClientTimeout(total=max(10.0, min(timeout_sec, 120.0)))
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            OPENROUTER_MODELS_URL,
            headers={"Accept": "application/json"},
        ) as resp:
            if resp.status != 200:
                txt = await resp.text()
                raise RuntimeError(f"OpenRouter models HTTP {resp.status}: {txt[:300]}")
            data = await resp.json()
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    return [x for x in rows if isinstance(x, dict)]


async def get_openrouter_models_catalog(
    *,
    force_refresh: bool = False,
    timeout_sec: float = 45.0,
) -> List[Dict[str, Any]]:
    """Список нормализованных записей (с кэшем)."""
    global _cache
    ttl = _cache_ttl_sec()
    now = time.monotonic()
    async with _cache_lock:
        if not force_refresh and _cache is not None:
            ts, rows = _cache
            if now - ts < ttl:
                return list(rows)
        raw = await fetch_openrouter_models_raw(timeout_sec=timeout_sec)
        norm = [normalize_model_record(x) for x in raw]
        _cache = (now, norm)
        return list(norm)


def sort_models_for_display(models: List[Dict[str, Any]], *, prefer_free: bool = True) -> List[Dict[str, Any]]:
    def key(m: Dict[str, Any]):
        free = 0 if m.get("likely_free_route") else 1
        ctx = m.get("context_length") or 0
        try:
            ctx_i = int(ctx)
        except (TypeError, ValueError):
            ctx_i = 0
        pid = str(m.get("id") or "")
        return (free, -ctx_i, pid)

    out = sorted(models, key=key)
    if not prefer_free:
        out = sorted(models, key=lambda m: str(m.get("id") or ""))
    return out


async def openrouter_completion_benchmark(
    *,
    api_key: str,
    model: str,
    max_tokens: int = 64,
    timeout_sec: float = 90.0,
) -> Dict[str, Any]:
    """
    Короткий completion для оценки скорости. Метрики:
    - completion_tokens_per_sec = completion_tokens / wall_s (оценка скорости выдачи)
    Ожидаемая длительность длинного ответа ≈ TTFT + completion_tokens / max(1e-6, tok_per_s).
    """
    key = (api_key or "").strip()
    if not key:
        return {"ok": False, "error": "missing_api_key"}
    url = (os.getenv("OPENROUTER_API_URL") or "https://openrouter.ai/api/v1/chat/completions").strip()
    payload = {
        "model": model.strip(),
        "messages": [
            {
                "role": "user",
                "content": "Write exactly 5 short numbered lines about network latency. No intro.",
            }
        ],
        "max_tokens": max(16, min(max_tokens, 512)),
        "temperature": 0.3,
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/ManSio/gemma_agent",
        "X-Title": "Gemma Agent Benchmark",
    }
    t0 = time.perf_counter()
    timeout = aiohttp.ClientTimeout(total=max(20.0, min(timeout_sec, 180.0)))
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                raw = await resp.text()
                wall_s = max(time.perf_counter() - t0, 1e-6)
                wall_ms = wall_s * 1000.0
                if resp.status != 200:
                    return {
                        "ok": False,
                        "http_status": resp.status,
                        "error": raw[:500],
                        "wall_ms": round(wall_ms, 2),
                        "model": model,
                    }
                data = json.loads(raw) if raw else {}
                usage = data.get("usage") if isinstance(data, dict) else {}
                ch = (data.get("choices") or [{}])[0]
                msg = ch.get("message") if isinstance(ch, dict) else {}
                content = (msg.get("content") or "") if isinstance(msg, dict) else ""
                ct = usage.get("completion_tokens") if isinstance(usage, dict) else None
                pt = usage.get("prompt_tokens") if isinstance(usage, dict) else None
                try:
                    ct_i = int(ct) if ct is not None else 0
                    pt_i = int(pt) if pt is not None else 0
                except (TypeError, ValueError):
                    ct_i = pt_i = 0
                ct_rate = round(ct_i / wall_s, 3) if ct_i else None
                total_rate = round((ct_i + pt_i) / wall_s, 3) if (ct_i + pt_i) else None
                return {
                    "ok": True,
                    "model": model,
                    "routed_model": data.get("model") if isinstance(data, dict) else None,
                    "wall_ms": round(wall_ms, 2),
                    "max_tokens_requested": payload["max_tokens"],
                    "usage": usage if isinstance(usage, dict) else {},
                    "completion_tokens": ct_i,
                    "prompt_tokens": pt_i,
                    "completion_tokens_per_sec": ct_rate,
                    "total_tokens_per_sec": total_rate,
                    "content_chars": len(content),
                    "hint": (
                        "Для длинного ответа ожидаемое время ≈ (completion_tokens / completion_tokens_per_sec) "
                        "плюс накладные расходы; 45+ с нормально при тысячах токенов."
                    ),
                }
    except Exception as e:
        logger.warning("openrouter_completion_benchmark: %s", e)
        return {"ok": False, "error": str(e), "model": model}
