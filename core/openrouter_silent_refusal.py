"""
Пустой content от OpenRouter при content_filter / moderation — retry на fallback-модель.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

_SILENT_FINISH_REASONS = frozenset(
    {
        "content_filter",
        "safety",
        "content_policy",
        "moderation",
        "refusal",
        "blocked",
    }
)


def silent_refusal_retry_enabled() -> bool:
    raw = (os.getenv("OPENROUTER_SILENT_REFUSAL_RETRY") or "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def silent_refusal_fallback_model(requested_model: str) -> str:
    override = (os.getenv("OPENROUTER_SILENT_REFUSAL_FALLBACK_MODEL") or "").strip()
    if override:
        return override
    fb = (os.getenv("OPENROUTER_NO_BALANCE_FALLBACK_MODEL") or "openrouter/free").strip()
    if fb and fb != (requested_model or "").strip():
        return fb
    free = (os.getenv("OPENROUTER_MODEL_FREE") or "").strip()
    if free and free != (requested_model or "").strip():
        return free
    return "openrouter/free"


def finish_reason_from_choice(choice: Any) -> str:
    if not isinstance(choice, dict):
        return ""
    fr = str(choice.get("finish_reason") or "").strip().lower()
    if fr:
        return fr
    err = choice.get("error")
    if isinstance(err, dict):
        code = str(err.get("code") or err.get("type") or "").strip().lower()
        if code:
            return code
    return ""


def completion_looks_like_silent_refusal(choice: Any, content: str) -> bool:
    """HTTP 200, но пользователю нечего показать (часто content_filter)."""
    if (content or "").strip():
        return False
    fr = finish_reason_from_choice(choice)
    if fr in _SILENT_FINISH_REASONS:
        return True
    if fr in ("length", "stop", "tool_calls", "function_call"):
        return False
    msg = choice.get("message") if isinstance(choice, dict) else None
    if isinstance(msg, dict):
        for key in ("refusal", "content_filter_results"):
            if msg.get(key):
                return True
    return False


def log_silent_refusal(*, requested_model: str, finish: str, tag: Optional[str]) -> None:
    logger.warning(
        "OpenRouter silent refusal (finish=%s model=%s tag=%s)",
        finish or "?",
        requested_model,
        tag or "-",
    )
