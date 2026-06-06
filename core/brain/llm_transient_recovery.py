"""Повтор LLM после локальных/transient сбоев (event loop, connector) — без «отмазок» в чат."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_LOCAL_TRANSIENT_RE = re.compile(
    r"(?i)event loop is closed|attached to a different loop|"
    r"connector is closed|session is closed|cannot connect to host"
)


def is_transient_llm_error(message: str) -> bool:
    s = (message or "").strip()
    if not s:
        return False
    if _LOCAL_TRANSIENT_RE.search(s):
        return True
    low = s.lower()
    if "timeout" in low or "timed out" in low:
        return True
    if "429" in s or "rate limit" in low:
        return True
    return False


def invalidate_openrouter_http_session() -> None:
    try:
        from core.openrouter_provider import get_openrouter_provider

        prov = get_openrouter_provider()
        prov._http_session = None
        prov._http_lock = None
        prov._http_loop_id = None
    except Exception as e:
        logger.debug("invalidate_openrouter_http_session: %s", e)


async def retry_openrouter_generate(
    llm: Any,
    gen_kw: Dict[str, Any],
    *,
    timeout_sec: float,
    tag: str,
) -> Dict[str, Any]:
    """Один повтор generate после сброса HTTP-сессии."""
    from core.resilience import with_timeout

    invalidate_openrouter_http_session()
    try:
        return await with_timeout(
            llm.generate(**gen_kw),
            timeout_sec=timeout_sec,
            tag=f"{tag}_retry",
        )
    except Exception as e:
        logger.warning("[%s] retry generate failed: %s", tag, e)
        return {"error": str(e), "content": ""}
