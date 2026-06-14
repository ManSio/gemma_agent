"""Хэши решений plan vs brain — drift alert при рассинхроне."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any, Dict, Optional

from core.monitoring import MONITOR

logger = logging.getLogger(__name__)


def turn_hash_drift_enabled() -> bool:
    """Включён ли drift-check plan_hash vs brain_hash."""
    raw = os.getenv("TURN_HASH_DRIFT_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _canonical_hash(payload: Dict[str, Any]) -> str:
    try:
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    except TypeError:
        blob = str(payload)
    return hashlib.sha256(blob.encode("utf-8", errors="replace")).hexdigest()[:16]


def plan_turn_hash(
    *,
    profile: str = "",
    lane: str = "",
    short_circuit: str = "",
    referent: str = "",
    module: str = "",
    intent: str = "",
    planner_bypass: str = "",
) -> str:
    """Хэш решений plan для сравнения с brain."""
    return _canonical_hash(
        {
            "profile": (profile or "").strip()[:48],
            "lane": (lane or "").strip()[:16],
            "short_circuit": (short_circuit or "").strip()[:48],
            "referent": (referent or "").strip()[:16],
            "module": (module or "").strip()[:48],
            "intent": (intent or "").strip()[:32],
            "planner_bypass": (planner_bypass or "").strip()[:48],
        }
    )


def plan_turn_hash_from_meta(input_meta: Optional[Dict[str, Any]], plan: Any = None) -> str:
    """Собрать plan hash из input_meta и plan steps."""
    meta = input_meta if isinstance(input_meta, dict) else {}
    tc = meta.get("turn_contract") if isinstance(meta.get("turn_contract"), dict) else {}
    tm = meta.get("plan_turn_meaning") if isinstance(meta.get("plan_turn_meaning"), dict) else {}
    module = ""
    intent = ""
    bypass = ""
    if plan is not None:
        try:
            steps = getattr(plan, "steps", None) or []
            if steps:
                module = str(getattr(steps[0], "module_name", "") or "")
                args = getattr(steps[0], "args", None) or {}
                if isinstance(args, dict):
                    bypass = str(args.get("fallback_variant") or "")
                    ctx = args.get("context")
                    if isinstance(ctx, dict):
                        ds = ctx.get("dialogue_state")
                        if isinstance(ds, dict):
                            intent = str(ds.get("last_intent") or "")
        except Exception as e:
            logger.debug("plan_turn_hash_from_meta: %s", e)
    return plan_turn_hash(
        profile=str(tc.get("sticky_profile") or meta.get("profile") or ""),
        lane=str(tc.get("lane") or ""),
        short_circuit=str(tc.get("short_circuit") or bypass or ""),
        referent=str(tm.get("referent") or tc.get("referent") or ""),
        module=module,
        intent=intent,
        planner_bypass=bypass,
    )


def brain_turn_hash(
    *,
    profile: str = "",
    lane: str = "",
    referent: str = "",
    hot_path_slim: bool = False,
    chat_context_slim: bool = False,
) -> str:
    """Хэш фактических решений brain."""
    return _canonical_hash(
        {
            "profile": (profile or "").strip()[:48],
            "lane": (lane or "").strip()[:16],
            "referent": (referent or "").strip()[:16],
            "hot_path_slim": bool(hot_path_slim),
            "chat_context_slim": bool(chat_context_slim),
        }
    )


def check_and_record_drift(
    *,
    plan_hash: str,
    brain_hash: str,
    trace_id: str = "",
) -> bool:
    """True если drift (hashes differ and оба непустые)."""
    if not turn_hash_drift_enabled():
        return False
    ph = (plan_hash or "").strip()
    bh = (brain_hash or "").strip()
    if not ph or not bh:
        return False
    drift = ph != bh
    if drift:
        MONITOR.inc("turn_hash_drift_total")
        logger.info(
            "[turn_hash] drift trace=%s plan=%s brain=%s",
            (trace_id or "")[:12],
            ph,
            bh,
        )
    else:
        MONITOR.inc("turn_hash_match_total")
    return drift
