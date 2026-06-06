"""
«Усталость»: по p95 латентности пайплайна/LLM — ужимаем промпт и ответ (без команд).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Tuple

from core.observability import OBS

logger = logging.getLogger(__name__)


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def fatigue_enabled() -> bool:
    return _truthy("AUTONOMIC_FATIGUE_ENABLED", True)


def fatigue_should_force_slim() -> bool:
    if not fatigue_enabled():
        return False
    try:
        t_pipe = float((os.getenv("FATIGUE_P95_TELEGRAM_MS") or "14000").strip() or "14000")
    except ValueError:
        t_pipe = 14000.0
    try:
        t_llm = float((os.getenv("FATIGUE_P95_OPENROUTER_MS") or "55000").strip() or "55000")
    except ValueError:
        t_llm = 55000.0
    snap = OBS.snapshot()
    lat = snap.get("latency_p95_ms") or {}
    if not isinstance(lat, dict):
        return False
    p_pipe = float(lat.get("telegram_pipeline") or 0.0)
    p_llm = float(lat.get("openrouter_completion_ms") or 0.0)
    return (t_pipe > 0 and p_pipe >= t_pipe) or (t_llm > 0 and p_llm >= t_llm)


def apply_fatigue_to_policy(behavior_policy: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    """
    Возвращает (policy, forced_slim). При усталости — verbosity concise.
    """
    if not fatigue_should_force_slim():
        return behavior_policy, False
    bp = dict(behavior_policy) if isinstance(behavior_policy, dict) else {}
    bp["verbosity"] = "concise"
    logger.debug("[autonomic] fatigue: force concise + slim prompt")
    return bp, True
