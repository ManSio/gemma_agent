"""
Safety config reader — reads config/safety.yml.
Provides cached accessors for all safety feature flags.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict

import yaml

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "safety.yml")
_cache_ts: float = 0.0
_cache: Dict[str, Any] = {}
_CACHE_TTL_SEC = 30.0


def _load_raw() -> Dict[str, Any]:
    global _cache, _cache_ts
    now = time.time()
    if _cache and (now - _cache_ts) < _CACHE_TTL_SEC:
        return _cache
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except Exception:
        raw = {}
    _cache = raw if isinstance(raw, dict) else {}
    _cache_ts = now
    return _cache


def _cfg() -> Dict[str, Any]:
    r = _load_raw()
    s = r.get("safety")
    return s if isinstance(s, dict) else {}


def tool_guard_enabled() -> bool:
    return bool(_cfg().get("tool_guard_enabled", False))


def context_reset_enabled() -> bool:
    return bool(_cfg().get("context_reset_enabled", False))


def reasoning_reset_enabled() -> bool:
    return bool(_cfg().get("reasoning_reset_enabled", False))


def subject_decay_enabled() -> bool:
    return bool(_cfg().get("subject_decay_enabled", False))


def memory_recall_guard_enabled() -> bool:
    return bool(_cfg().get("memory_recall_guard_enabled", False))


def kv_session_reset_enabled() -> bool:
    return bool(_cfg().get("kv_session_reset_enabled", False))


def fast_path_safety_enabled() -> bool:
    return bool(_cfg().get("fast_path_safety_enabled", False))


def max_reasoning_ms() -> int:
    return max(100, int(_cfg().get("max_reasoning_ms", 5000)))


def noise_sequence_limit() -> int:
    return max(3, int(_cfg().get("noise_sequence_limit", 15)))
