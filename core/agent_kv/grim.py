"""
Grim-trigger: состояние наказания в KV, слияние с CDC policy (tier / принудительный диалог).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from core.agent_kv.store import agent_kv_branch, get_json, set_json
from core.runtime_telegram_settings import effective_bool

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def grim_enabled() -> bool:
    return effective_bool("GRIM_TRIGGER_ENABLED", default=True)


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or str(default)).strip() or str(default))
    except ValueError:
        return default


def merge_grim_policy_into(cdc_policy: Dict[str, Any], grim_state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out = dict(cdc_policy or {})
    g = grim_state if isinstance(grim_state, dict) else {}
    if not g.get("active"):
        out.pop("grim_active", None)
        out.pop("grim_tier_ceiling", None)
        out.pop("grim_force_dialog", None)
        out.pop("grim_level", None)
        return out
    out["grim_active"] = True
    out["grim_level"] = str(g.get("level") or "firm")
    ceil = str(g.get("tier_ceiling") or "")
    if ceil in ("shallow", "nested", "deep"):
        out["grim_tier_ceiling"] = ceil
    if g.get("force_dialog"):
        out["grim_force_dialog"] = True
    return out


def apply_grim_to_policy(user_id: str, policy: Dict[str, Any]) -> Dict[str, Any]:
    if not grim_enabled():
        return policy
    uid = str(user_id or "").strip()
    if not uid:
        return policy
    g = get_json("grim", uid, branch=agent_kv_branch())
    return merge_grim_policy_into(policy, g)


def update_grim_after_turn(
    user_id: str,
    *,
    outcome: str,
    agg_bucket: Dict[str, Any],
    module: str,
    intent: str,
) -> None:
    if not grim_enabled():
        return
    uid = str(user_id or "").strip()
    if not uid:
        return
    br = agent_kv_branch()
    enter = max(2, _env_int("GRIM_ENTER_FAIL_STREAK", 5))
    forgive = max(1, _env_int("GRIM_FORGIVE_SUCCESS_STREAK", 2))
    ttl_grim = max(60, _env_int("GRIM_STATE_TTL_SEC", 86400 * 14))
    fs = int(agg_bucket.get("fail_streak") or 0)
    ss = int(agg_bucket.get("success_streak") or 0)
    o = (outcome or "").strip()
    prev = get_json("grim", uid, branch=br) or {}
    active = bool(prev.get("active"))
    level = str(prev.get("level") or "firm")

    if o == "ok" and ss >= forgive and active:
        set_json(
            "grim",
            uid,
            {
                "active": False,
                "level": "none",
                "cleared_ts": _now_iso(),
                "reason": "success_streak",
            },
            branch=br,
            ttl_sec=ttl_grim,
            priority=100,
        )
        return

    if fs >= enter and o != "ok":
        new_level = "hard" if fs >= enter + 2 else "firm"
        until = (datetime.now(timezone.utc) + timedelta(seconds=ttl_grim)).isoformat(timespec="seconds")
        set_json(
            "grim",
            uid,
            {
                "active": True,
                "level": new_level,
                "tier_ceiling": "nested" if new_level == "firm" else "shallow",
                "force_dialog": new_level == "hard",
                "since_ts": _now_iso(),
                "until_ts": until,
                "reason": f"fail_streak_{fs}",
                "last_module": str(module or ""),
                "last_intent": str(intent or ""),
            },
            branch=br,
            ttl_sec=ttl_grim,
            priority=100,
        )
        try:
            from core.monitoring import MONITOR

            MONITOR.inc("grim_trigger_activated_total")
        except Exception as e:
            logger.debug('%s optional failed: %s', 'grim', e, exc_info=True)
def hydrate_cdc_from_kv(user_id: Optional[str], persisted: Any) -> Dict[str, Any]:
    """Подмешать cdc_policy и grim из KV в снимок BehaviorStore перед планированием."""
    from core.agent_kv.store import agent_kv_enabled, get_json

    if not agent_kv_enabled() or not user_id:
        return dict(persisted) if isinstance(persisted, dict) else {}
    p = dict(persisted) if isinstance(persisted, dict) else {}
    uid = str(user_id).strip()
    br = agent_kv_branch()
    pol = get_json("cdc_policy", uid, branch=br)
    if isinstance(pol, dict) and pol:
        p["cdc_policy"] = pol
    g = get_json("grim", uid, branch=br)
    if isinstance(g, dict) and g:
        base = dict(p.get("cdc_policy") or {})
        p["cdc_policy"] = merge_grim_policy_into(base, g)
    return p
