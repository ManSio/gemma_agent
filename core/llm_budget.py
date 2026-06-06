"""
Token Budget Manager — estimates tokens, triggers delta-prompting,
model switching, context collapse and KV resets.
v1.0.0
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

LLM_BUDGET_VERSION = "1.0.0"


def _f(name: str, default: float) -> float:
    try:
        return float((os.getenv(name) or str(default)).strip())
    except ValueError:
        return default


def _i(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or str(default)).strip())
    except ValueError:
        return default


def _b(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def estimate_tokens(context: Optional[Dict[str, Any]], user_input: str) -> int:
    """Rough token estimate: ~1.3 chars per token for Russian/English mix."""
    total_chars = 0
    if isinstance(context, dict):
        try:
            import json
            total_chars += len(json.dumps(context, ensure_ascii=False, default=str))
        except Exception as e:
            logger.debug('%s optional failed: %s', 'llm_budget', e, exc_info=True)
    total_chars += len(str(user_input or ""))
    est = int(total_chars / 1.3)
    tokens_per_sec = _f("BRAIN_LLM_TOKENS_PER_SEC_EST", 15.0)
    max_tokens = _i("OPENROUTER_GEN_MAX_TOKENS", 4096)
    return min(est + max_tokens, 200000)


def should_use_delta_prompting(prev_context: Optional[Dict[str, Any]], new_context: Dict[str, Any]) -> bool:
    """Check if delta-prompting (diff-based) should be used.
    Deterministic key-overlap comparison: threshold ≥ 0.70."""
    if prev_context is None:
        return False
    try:
        prev_keys = set(prev_context.keys())
        new_keys = set(new_context.keys())
        if not prev_keys or not new_keys:
            return False
        overlap = len(prev_keys & new_keys)
        union = len(prev_keys | new_keys)
        key_similarity = overlap / union if union > 0 else 0.0
        return key_similarity >= 0.70
    except Exception:
        return False


def similar(prev_context: Optional[Dict[str, Any]], new_context: Dict[str, Any]) -> bool:
    """Check if two contexts are similar enough for delta-prompting."""
    return should_use_delta_prompting(prev_context, new_context)


def build_delta(prev_context: Optional[Dict[str, Any]], new_context: Dict[str, Any]) -> Dict[str, Any]:
    """Build delta context: return only changed parts between prev and new."""
    return apply_delta_prompting(prev_context, new_context)


def apply_delta_prompting(prev_context: Optional[Dict[str, Any]], new_context: Dict[str, Any]) -> Dict[str, Any]:
    """Build delta context: if small diff, return only changed parts."""
    if prev_context is None:
        return dict(new_context)
    try:
        prev_keys = set(prev_context.keys())
        new_keys = set(new_context.keys())
        delta: Dict[str, Any] = {"__delta__": True}
        for key in new_keys - prev_keys:
            delta[key] = new_context[key]
        for key in prev_keys & new_keys:
            pv = prev_context.get(key)
            nv = new_context.get(key)
            if pv != nv:
                delta[key] = nv
        if len(delta) <= 2:  # only __delta__ and maybe 1 key
            return dict(new_context)
        return delta
    except Exception:
        return dict(new_context)


def should_switch_model(free_tokens: int, threshold: Optional[int] = None) -> bool:
    """Check if we should switch model based on free tokens budget."""
    if threshold is None:
        threshold = _i("MODEL_SWITCH_THRESHOLD", 50000)
    threshold = max(1000, threshold)
    return free_tokens > 0 and free_tokens < threshold


def should_collapse(context: Optional[Dict[str, Any]]) -> bool:
    """Check if context should be collapsed (too large)."""
    if not isinstance(context, dict):
        return False
    est = estimate_tokens(context, "")
    first_max = _i("BRAIN_FIRST_MAX_TOKENS", 8192)
    second_max = _i("BRAIN_SECOND_MAX_TOKENS", 16384)
    max_prompt = max(first_max, second_max)
    return est > max_prompt


def get_model_switch_threshold() -> int:
    return _i("MODEL_SWITCH_THRESHOLD", 50000)


def get_max_tokens_primary() -> int:
    return _i("BRAIN_FIRST_MAX_TOKENS", 8192)


def get_max_tokens_secondary() -> int:
    return _i("BRAIN_SECOND_MAX_TOKENS", 16384)


def to_prompt() -> str:
    """Budget stays internal — NOT injected into LLM prompt.
    Token budgeting is a code-level mechanism, not a prompt-level one."""
    return ""
