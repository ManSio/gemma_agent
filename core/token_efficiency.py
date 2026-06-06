"""
Token Efficiency Engine — config loader and feature flag checks.
Reads config/token_efficiency.yml, provides cached accessors.
All features off by default; controlled entirely by the YAML config file.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

import yaml

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "token_efficiency.yml")
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
    te = r.get("token_efficiency")
    return te if isinstance(te, dict) else {}


# ── Feature flag accessors ──

def kv_reuse_enabled() -> bool:
    kv = _cfg().get("kv_reuse")
    return bool((kv or {}).get("enabled", False))


def kv_reuse_max_age_ms() -> int:
    kv = _cfg().get("kv_reuse")
    return max(0, int((kv or {}).get("max_age_ms", 600000)))


def kv_reuse_max_turns() -> int:
    kv = _cfg().get("kv_reuse")
    return max(1, int((kv or {}).get("max_turns", 20)))


def delta_enabled() -> bool:
    d = _cfg().get("delta")
    return bool((d or {}).get("enabled", False))


def delta_min_change_chars() -> int:
    d = _cfg().get("delta")
    return max(0, int((d or {}).get("min_change_chars", 32)))


def cache_enabled() -> bool:
    c = _cfg().get("cache")
    return bool((c or {}).get("enabled", False))


def cache_ttl_seconds() -> int:
    c = _cfg().get("cache")
    return max(1, int((c or {}).get("ttl_seconds", 900)))


def cache_max_entries() -> int:
    c = _cfg().get("cache")
    return max(1, int((c or {}).get("max_entries", 500)))


def collapse_enabled() -> bool:
    c = _cfg().get("collapse")
    return bool((c or {}).get("enabled", False))


def collapse_max_prompt_tokens() -> int:
    c = _cfg().get("collapse")
    return max(100, int((c or {}).get("max_prompt_tokens", 8000)))


def collapse_history_window() -> int:
    c = _cfg().get("collapse")
    return max(1, int((c or {}).get("history_window", 6)))


def tools_batch_enabled() -> bool:
    t = _cfg().get("tools")
    return bool((t or {}).get("batch_enabled", False))


def budget_enabled() -> bool:
    b = _cfg().get("budget")
    return bool((b or {}).get("enabled", True))


def budget_hard_limit_tokens() -> int:
    b = _cfg().get("budget")
    return max(100, int((b or {}).get("hard_limit_tokens", 12000)))


def compactor_enabled() -> bool:
    c = _cfg().get("compactor")
    return bool((c or {}).get("enabled", False))


def compactor_threshold() -> float:
    c = _cfg().get("compactor")
    return float((c or {}).get("threshold", 0.8))


def compactor_max_summary_tokens() -> int:
    c = _cfg().get("compactor")
    return max(20, int((c or {}).get("max_summary_tokens", 200)))
