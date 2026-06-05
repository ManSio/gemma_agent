"""
Нервная система агента (рефлексы поверх event-bus).

Фаза 1:
- слушает turn.outcome
- при серии bad-исходов по маршруту применяет мгновенный reflex:
  пишет next_turn_tier_floor в cdc_policy через KV
"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
import os
import threading
from typing import Any, Dict

from core.event_bus import bus

_installed = False
_lock = threading.RLock()
_route_bad_streak: Dict[str, deque[int]] = defaultdict(lambda: deque(maxlen=12))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or str(default)).strip() or str(default))
    except ValueError:
        return default


def _on_turn_outcome(evt: Dict[str, Any]) -> None:
    uid = str(evt.get("user_id") or "").strip()
    if not uid:
        return
    module = str(evt.get("module") or "").strip().replace("-", "_").lower()
    intent = str(evt.get("intent") or "").strip() or "unknown"
    outcome = str(evt.get("outcome") or "").strip()
    route = f"{module}|{intent}"
    rk = f"{uid}|{route}"
    bad = 0 if outcome == "ok" else 1
    with _lock:
        dq = _route_bad_streak[rk]
        dq.append(bad)
        streak = 0
        for x in reversed(dq):
            if x == 1:
                streak += 1
            else:
                break
    threshold = max(2, _env_int("NERVOUS_REFLEX_BAD_STREAK", 3))
    if streak < threshold:
        return
    try:
        from core.agent_kv.store import agent_kv_branch, agent_kv_enabled, get_json, set_json

        if not agent_kv_enabled():
            return
        br = agent_kv_branch()
        pol = get_json("cdc_policy", uid, branch=br) or {}
        caps = dict(pol.get("route_tier_caps") or {})
        caps[route] = "nested"
        pol["route_tier_caps"] = caps
        pol["next_turn_tier_floor"] = "nested"
        pol["route_hint_level"] = "strong"
        pol["reflex_last_applied"] = _now_iso()
        pol["reflex_reason"] = f"bad_streak_{streak}"
        set_json("cdc_policy", uid, pol, branch=br, ttl_sec=None, priority=90)
        bus.emit(
            "reflex.applied",
            {
                "user_id": uid,
                "route": route,
                "streak": streak,
                "action": "set_next_turn_tier_floor_nested",
            },
        )
    except Exception:
        return


def install_nervous_system() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    bus.subscribe("turn.outcome", _on_turn_outcome)

