"""
LLM Self-Healing Engine — detects LLM response anomalies and applies
recovery strategies: runaway reasoning, invalid JSON, tool errors,
bad routing, model failure, context reset.
v1.0.0
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

LLM_SELF_HEAL_VERSION = "1.0.0"

_ANOMALY_COUNTERS: Dict[str, int] = {}
_RECOVERY_LOG: List[Dict[str, Any]] = []


def _b(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


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


def _record(reason: str, details: str = "") -> None:
    global _ANOMALY_COUNTERS
    _ANOMALY_COUNTERS[reason] = _ANOMALY_COUNTERS.get(reason, 0) + 1
    _RECOVERY_LOG.append({
        "reason": reason,
        "details": str(details)[:300],
        "ts": time.time(),
    })
    if len(_RECOVERY_LOG) > 200:
        _RECOVERY_LOG[:] = _RECOVERY_LOG[-100:]
    # Register error with resilience controller
    try:
        from core.resilience_controller import ResilienceController
        rc = ResilienceController()
        if rc.is_enabled():
            # ResilienceController does not have register_error directly,
            # but error_analysis.record_error_event is the canonical path.
            from core.error_analysis import record_error_event
            record_error_event("llm_self_heal", reason, extra={"details": str(details)[:200]})
    except Exception as e:
        logger.debug('%s optional failed: %s', 'llm_self_heal', e, exc_info=True)
    # Register recovery with self-healing module
    try:
        from core.self_healing import log_tool_error
        log_tool_error("llm_self_heal", 0, reason)
    except Exception as e:
        logger.debug('%s optional failed: %s', 'llm_self_heal', e, exc_info=True)
def detect_runaway_reasoning(response: Optional[Dict[str, Any]], latency: float) -> bool:
    """Detect if LLM response shows signs of runaway reasoning (too long, repetitive)."""
    if not isinstance(response, dict):
        return False
    content = str(response.get("content") or "")
    max_latency = _f("BRAIN_LLM_PREMIUM_TIMEOUT_SEC", 120.0)
    if latency > max_latency:
        _record("runaway_latency", f"latency={latency:.1f}s > {max_latency:.1f}s")
        return True
    if len(content) > 8000:
        repeat_pattern = _find_repetition(content)
        if repeat_pattern:
            _record("runaway_repetition", f"pattern={repeat_pattern[:80]}")
            return True
    return False


def detect_invalid_json(response: Optional[Dict[str, Any]]) -> bool:
    """Detect if LLM response contains invalid JSON blocking."""
    if not isinstance(response, dict):
        return False
    content = str(response.get("content") or "")
    if "TOOL_CALL:" not in content and not content.strip().startswith("{"):
        return False
    json_candidates = _extract_json_blocks(content)
    for candidate in json_candidates:
        try:
            json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            _record("invalid_json", f"len={len(candidate)}")
            return True
    return False


def detect_tool_error(response: Optional[Dict[str, Any]]) -> bool:
    """Detect tool-call errors in LLM response."""
    if not isinstance(response, dict):
        return False
    content = str(response.get("content") or "")
    error_markers = ["TOOL_ERROR:", "tool_failed", "execution_error", "инструмент не найден"]
    if any(m in content for m in error_markers):
        _record("tool_error", f"content_chars={len(content)}")
        return True
    tool_calls = response.get("tool_calls")
    if isinstance(tool_calls, list):
        for tc in tool_calls:
            if isinstance(tc, dict) and tc.get("error"):
                _record("tool_error", f"tool={tc.get('tool', '?')}")
                return True
    return False


def detect_bad_routing(response: Optional[Dict[str, Any]]) -> bool:
    """Detect if LLM was routed incorrectly (empty, too generic, wrong format)."""
    if not isinstance(response, dict):
        return False
    content = str(response.get("content") or "").strip()
    if not content or len(content) < 3:
        _record("bad_routing_empty")
        return True
    bad_patterns = ["я не знаю", "не могу ответить", "переформулируйте", "уточните вопрос"]
    lower = content.lower()
    if all(p in lower for p in bad_patterns[:2]) and len(content) < 300:
        _record("bad_routing_confused", f"len={len(content)}")
        return True
    return False


def apply_recovery_strategy(reason: str) -> Dict[str, Any]:
    """Determine recovery action based on anomaly reason."""
    strategies: Dict[str, Dict[str, Any]] = {
        "runaway_latency": {"action": "fallback_model", "depth": "shallow", "reset_kv": True},
        "runaway_repetition": {"action": "fallback_model", "depth": "shallow", "reset_kv": True},
        "invalid_json": {"action": "retry_free", "depth": "medium", "reset_kv": False},
        "tool_error": {"action": "retry_free", "depth": "shallow", "reset_kv": False},
        "bad_routing_empty": {"action": "fallback_model", "depth": "shallow", "reset_kv": False},
        "bad_routing_confused": {"action": "retry_free", "depth": "shallow", "reset_kv": False},
    }
    r = str(reason or "").strip().lower()
    return strategies.get(r, {"action": "retry_free", "depth": "shallow", "reset_kv": False})


def switch_model_on_failure() -> Optional[str]:
    """Switch to fallback model on failure. Returns new model name or None."""
    if not _b("BRAIN_LLM_TIERED_RETRY", True):
        return None
    attempts = _ANOMALY_COUNTERS.get("runaway_latency", 0) + _ANOMALY_COUNTERS.get("runaway_repetition", 0)
    free_attempts = _i("BRAIN_LLM_FREE_ATTEMPTS", 3)
    if attempts >= free_attempts:
        from core.model_profile import resolve_brain_secondary_model
        try:
            return resolve_brain_secondary_model("openrouter")
        except Exception as e:
            logger.debug('%s optional failed: %s', 'llm_self_heal', e, exc_info=True)
    return None


def reset_context_on_failure() -> bool:
    """Check if context should be reset after failure."""
    total = sum(_ANOMALY_COUNTERS.values())
    return total >= 3


def is_enabled() -> bool:
    return _b("RESILIENCE_AUTONOMY_ENABLED", True)


def is_cdc_enabled() -> bool:
    return _b("CDC_ENGINE_ENABLED", True)


def is_route_risk_enabled() -> bool:
    return _b("ROUTE_RISK_MEMORY_ENABLED", True)


def get_stats() -> Dict[str, Any]:
    return {
        "counters": dict(_ANOMALY_COUNTERS),
        "recent_recoveries": _RECOVERY_LOG[-10:] if _RECOVERY_LOG else [],
        "total_recoveries": len(_RECOVERY_LOG),
        "context_reset_pending": reset_context_on_failure(),
    }


def reset_counters() -> None:
    global _ANOMALY_COUNTERS
    _ANOMALY_COUNTERS.clear()
    _RECOVERY_LOG.clear()


def to_prompt() -> str:
    """Self-healing stays internal — errors are NOT injected into LLM prompt.
    Recovery strategies are code-level, not prompt-level."""
    return ""


def _find_repetition(text: str) -> Optional[str]:
    """Detect repeating n-gram patterns in text."""
    if len(text) < 200:
        return None
    for n in (20, 30, 50):
        window = text[-min(len(text), n * 3):]
        for i in range(0, len(window) - n * 2, n):
            chunk = window[i:i + n]
            if window.count(chunk) >= 2:
                return chunk[:50]
    return None


def _extract_json_blocks(text: str) -> List[str]:
    """Extract JSON blocks from text."""
    blocks = []
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                blocks.append(text[start:i + 1])
                start = -1
    return blocks
