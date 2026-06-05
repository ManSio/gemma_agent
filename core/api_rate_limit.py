"""
Ограничение частоты тяжёлых HTTP-вызовов (chat, bot-relay, ops/probe).

Защита от перегрузки при прогонах agent_probe_http / Cursor-агентом.
Пороги — только из .env (см. .env.example).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

_lock = asyncio.Lock()
_window_events: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=256))
_last_call: Dict[str, float] = {}


def api_rate_limit_enabled() -> bool:
    return os.getenv("API_RATE_LIMIT_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def api_rate_limit_heavy_rpm() -> int:
    return max(1, int(os.getenv("API_RATE_LIMIT_HEAVY_RPM", "6")))


def api_rate_limit_heavy_min_interval_sec() -> float:
    return max(0.0, float(os.getenv("API_RATE_LIMIT_HEAVY_MIN_INTERVAL_SEC", "10")))


def client_host_from_request(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded
    if request.client and request.client.host:
        return str(request.client.host)
    return "unknown"


def _rate_key(*, scope: str, client_host: str, user_id: Optional[str]) -> str:
    uid = (user_id or "").strip() or "_"
    return f"{scope}:{client_host}:{uid}"


async def assert_api_heavy_rate_limit(
    request: Request,
    *,
    user_id: Optional[str] = None,
    scope: str = "heavy",
) -> None:
    """429 + Retry-After при превышении RPM или min interval."""
    if not api_rate_limit_enabled():
        return

    host = client_host_from_request(request)
    key = _rate_key(scope=scope, client_host=host, user_id=user_id)
    now = time.monotonic()
    rpm = api_rate_limit_heavy_rpm()
    min_gap = api_rate_limit_heavy_min_interval_sec()
    window_sec = 60.0

    async with _lock:
        if min_gap > 0:
            prev = _last_call.get(key)
            if prev is not None and (now - prev) < min_gap:
                retry = max(1, int(min_gap - (now - prev) + 0.5))
                logger.info(
                    "api_rate_limit min_interval key=%s retry_after=%ss",
                    key,
                    retry,
                )
                raise HTTPException(
                    status_code=429,
                    detail=f"API rate limit: min {min_gap:.0f}s between heavy requests",
                    headers={"Retry-After": str(retry)},
                )

        dq = _window_events[key]
        cutoff = now - window_sec
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= rpm:
            oldest = dq[0] if dq else now
            retry = max(1, int(window_sec - (now - oldest) + 0.5))
            logger.info(
                "api_rate_limit rpm key=%s count=%d rpm=%d retry_after=%ss",
                key,
                len(dq),
                rpm,
                retry,
            )
            raise HTTPException(
                status_code=429,
                detail=f"API rate limit: max {rpm} heavy requests per minute",
                headers={"Retry-After": str(retry)},
            )

        dq.append(now)
        _last_call[key] = now
