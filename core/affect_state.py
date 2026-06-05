"""
Affect-state: внутреннее состояние агента (confidence/caution/fatigue/focus).
Хранится в Agent KV и модулирует выбор task_tier.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Any, Dict, Optional

from core.runtime_telegram_settings import effective_bool


def affect_enabled() -> bool:
    return effective_bool("AFFECT_STATE_ENABLED", default=True)


def _env_float(name: str, default: float) -> float:
    try:
        return float((os.getenv(name) or str(default)).strip() or str(default))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or str(default)).strip() or str(default))
    except ValueError:
        return default


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def default_affect_state() -> Dict[str, Any]:
    return {
        "confidence": 0.5,
        "caution": 0.5,
        "fatigue": 0.25,
        "focus": 0.5,
        "updated_ts": _now_iso(),
    }


def hydrate_affect_from_kv(user_id: Optional[str], persisted: Dict[str, Any]) -> Dict[str, Any]:
    if not affect_enabled() or not user_id:
        return persisted
    try:
        from core.agent_kv.store import agent_kv_branch, agent_kv_enabled, get_json

        if not agent_kv_enabled():
            return persisted
        p = dict(persisted or {})
        row = get_json("affect", str(user_id).strip(), branch=agent_kv_branch())
        if isinstance(row, dict) and row:
            p["affect_state"] = row
        return p
    except Exception:
        return persisted


def update_affect_after_turn(
    *,
    user_id: Optional[str],
    outcome: str,
    task_tier: str,
    error_type: str = "",
) -> Dict[str, Any]:
    if not affect_enabled() or not user_id:
        return {}
    try:
        from core.agent_kv.store import agent_kv_branch, agent_kv_enabled, get_json, set_json

        if not agent_kv_enabled():
            return {}
        uid = str(user_id).strip()
        br = agent_kv_branch()
        cur = get_json("affect", uid, branch=br) or default_affect_state()
        c = float(cur.get("confidence") or 0.5)
        z = float(cur.get("caution") or 0.5)
        f = float(cur.get("fatigue") or 0.25)
        k = float(cur.get("focus") or 0.5)

        o = (outcome or "").strip()
        bad = o != "ok"
        if bad:
            c -= 0.10
            z += 0.12
            f += 0.06
            k -= 0.04
        else:
            c += 0.08
            z -= 0.05
            f += 0.02
            k += 0.03
        et = (error_type or "").strip()
        if bad and et in {"policy", "router"}:
            z += 0.08
        if (task_tier or "").strip() == "deep":
            f += 0.03
        if o == "clarify":
            z += 0.02
            c -= 0.03

        out = {
            "confidence": _clamp01(c),
            "caution": _clamp01(z),
            "fatigue": _clamp01(f),
            "focus": _clamp01(k),
            "updated_ts": _now_iso(),
            "last_outcome": o,
            "last_error_type": et or "unknown",
        }
        ttl = max(3600, _env_int("AFFECT_STATE_TTL_SEC", 2592000))
        set_json("affect", uid, out, branch=br, ttl_sec=ttl, priority=35)
        return out
    except Exception:
        return {}


def modulate_task_tier_with_affect(tier: str, affect_state: Optional[Dict[str, Any]]) -> str:
    """
    Эмоциональные модуляторы:
    - высокая осторожность/усталость -> потолок nested/shallow
    - высокая уверенность+фокус при низкой усталости -> можно поднять shallow -> nested
    """
    t = (tier or "shallow").strip() or "shallow"
    s = affect_state if isinstance(affect_state, dict) else {}
    if not s:
        return t
    caution = float(s.get("caution") or 0.5)
    fatigue = float(s.get("fatigue") or 0.25)
    conf = float(s.get("confidence") or 0.5)
    focus = float(s.get("focus") or 0.5)

    from core.task_depth import apply_tier_ceiling, max_task_tier

    hard = _env_float("AFFECT_CAUTION_HARD_CEIL", 0.92)
    soft = _env_float("AFFECT_CAUTION_SOFT_CEIL", 0.75)
    fat_hard = _env_float("AFFECT_FATIGUE_HARD_CEIL", 0.9)
    fat_soft = _env_float("AFFECT_FATIGUE_SOFT_CEIL", 0.72)
    boost_conf = _env_float("AFFECT_CONFIDENCE_BOOST", 0.8)
    boost_focus = _env_float("AFFECT_FOCUS_BOOST", 0.78)
    boost_fat = _env_float("AFFECT_FATIGUE_FOR_BOOST_MAX", 0.5)

    out = t
    if caution >= hard or fatigue >= fat_hard:
        out = apply_tier_ceiling(out, "shallow")
    elif caution >= soft or fatigue >= fat_soft:
        out = apply_tier_ceiling(out, "nested")
    if out == "shallow" and conf >= boost_conf and focus >= boost_focus and fatigue <= boost_fat:
        out = max_task_tier(out, "nested")
    return out

