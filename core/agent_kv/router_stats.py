"""Агрегированная статистика маршрутизатора в KV (intent × module × исход)."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict

from core.agent_kv.store import agent_kv_branch, get_json, set_json
from core.experience_memory import normalize_module_key

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record_router_turn(
    *,
    user_id: str,
    intent: str,
    module: str,
    outcome: str,
    task_tier: str = "",
) -> None:
    from core.agent_kv.store import agent_kv_enabled

    if not agent_kv_enabled():
        return
    uid = str(user_id or "").strip()
    if not uid:
        return
    try:
        ttl = max(3600, int((os.getenv("AGENT_KV_ROUTER_ROLLUP_TTL_SEC") or "2592000").strip() or "2592000"))
    except ValueError:
        ttl = 2592000
    br = agent_kv_branch()
    key = f"rollup|{uid}"
    r = get_json("router", key, branch=br) or {"routes": {}, "turns": 0, "updated_ts": ""}
    routes: Dict[str, Any] = dict(r.get("routes") or {})
    rk = f"{(intent or '').strip() or 'unknown'}|{normalize_module_key(module)}"
    slot = dict(routes.get(rk) or {})
    slot["n"] = int(slot.get("n") or 0) + 1
    slot["n_bad"] = int(slot.get("n_bad") or 0) + (0 if (outcome or "") == "ok" else 1)
    slot["last_outcome"] = (outcome or "")[:32]
    slot["last_tier"] = (task_tier or "")[:16]
    slot["last_ts"] = _now_iso()
    routes[rk] = slot
    out = {
        "routes": routes,
        "turns": int(r.get("turns") or 0) + 1,
        "updated_ts": _now_iso(),
    }
    try:
        pri = int((os.getenv("AGENT_KV_ROUTER_PRIORITY") or "20").strip() or "20")
    except ValueError:
        pri = 20
    set_json("router", key, out, branch=br, ttl_sec=ttl, priority=pri)
