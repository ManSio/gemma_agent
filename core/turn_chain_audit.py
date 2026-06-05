"""
Полная цепочка хода: маршрут → plan → execute → валидация → LLM telemetry → утечки.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.agent_test_validators import validate_reply
from core.text_leak_scan import scan_text_leaks


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def route_snapshot(user_text: str) -> Dict[str, Any]:
    snap: Dict[str, Any] = {}
    try:
        from core.brain.profile_route_guard import clamp_profile, preflight_profile

        snap["preflight_profile"] = preflight_profile(user_text)
        snap["clamp_from_math"] = clamp_profile("math_solve", user_text, router_confidence=0.98)
    except Exception as e:
        snap["route_error"] = str(e)[:200]
    try:
        from core.brain.text_helpers import is_bot_operational_diag_question

        snap["operational_diag"] = bool(is_bot_operational_diag_question(user_text))
    except Exception:
        pass
    return snap


def dialogue_snapshot(user_id: str, group_id: Optional[str] = None) -> Dict[str, Any]:
    try:
        from core.behavior_store import BehaviorStore

        rec = BehaviorStore().load(str(user_id), group_id)
        ds = rec.get("dialogue_state") if isinstance(rec.get("dialogue_state"), dict) else {}
        return {
            "last_intent": ds.get("last_intent"),
            "planned_module": ds.get("planned_module"),
            "brain_profile": ds.get("brain_profile") or ds.get("router_profile"),
            "router_profile": ds.get("router_profile"),
            "task_tier": ds.get("task_tier"),
            "planner_reason": (str(ds.get("planner_reason") or ""))[:200],
            "dialogue_lane": ds.get("dialogue_lane"),
        }
    except Exception as e:
        return {"error": str(e)[:200]}


def plan_snapshot(plan: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"steps": []}
    steps = getattr(plan, "steps", None) or []
    for st in steps[:5]:
        args = getattr(st, "args", None) or {}
        mod = getattr(st, "module", None) or getattr(st, "name", None)
        out["steps"].append(
            {
                "module": str(mod) if mod else None,
                "has_context": isinstance(args.get("context"), dict),
            }
        )
    if steps:
        ctx = (steps[0].args or {}).get("context") if hasattr(steps[0], "args") else {}
        if isinstance(ctx, dict):
            ds = ctx.get("dialogue_state") or {}
            out["context_profile"] = ds.get("brain_profile") or ds.get("router_profile")
            out["context_intent"] = ds.get("last_intent")
            out["context_module"] = ds.get("planned_module")
    return out


def llm_calls_since(t0: float, *, limit: int = 8) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        from core.llm_usage_store import load_records

        for r in reversed(load_records(max_lines=400)[-400:]):
            ts = r.get("ts")
            if ts is None:
                continue
            try:
                if isinstance(ts, (int, float)):
                    rt = float(ts)
                else:
                    from core.llm_usage_store import _parse_ts

                    dt = _parse_ts(r)
                    rt = dt.timestamp() if dt else 0
                if rt >= t0 - 0.5:
                    rows.append(
                        {
                            "ts": ts,
                            "tag": r.get("tag"),
                            "model": r.get("requested_model") or r.get("model"),
                            "upstream": r.get("upstream"),
                            "session_id": r.get("session_id"),
                            "prompt_tokens": r.get("prompt_tokens"),
                            "cached_prompt_tokens": r.get("cached_prompt_tokens"),
                            "completion_tokens": r.get("completion_tokens"),
                            "cost": r.get("cost"),
                            "latency_ms": r.get("latency_ms"),
                        }
                    )
            except Exception:
                continue
            if len(rows) >= limit:
                break
    except Exception:
        pass
    return list(reversed(rows))


def quality_snapshot(user_text: str, reply: str, user_id: str) -> Dict[str, Any]:
    try:
        from core.turn_quality_loop import audit_turn_payload

        audit = audit_turn_payload(
            {
                "user_excerpt": user_text[:240],
                "assistant_excerpt": reply[:480],
                "user_id": user_id,
                "outcome": "ok",
                "intent": "",
                "module": "chat-orchestrator",
            }
        )
        return {"issues": audit.get("issues") or [], "ok": audit.get("ok", True)}
    except Exception as e:
        return {"error": str(e)[:200]}


def trajectory_summary(
    user_id: str,
    user_text: str,
    plan: Any = None,
    group_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Плоская траектория для probe-отчёта (profile, intent, module, preflight)."""
    route = route_snapshot(user_text)
    dlg = dialogue_snapshot(user_id, group_id)
    plan_snap = plan_snapshot(plan) if plan is not None else {}
    profile = (
        dlg.get("brain_profile")
        or dlg.get("router_profile")
        or plan_snap.get("context_profile")
    )
    return {
        "preflight_profile": route.get("preflight_profile"),
        "operational_diag_question": route.get("operational_diag"),
        "profile": profile,
        "intent": dlg.get("last_intent") or plan_snap.get("context_intent"),
        "module": dlg.get("planned_module") or plan_snap.get("context_module"),
        "task_tier": dlg.get("task_tier"),
        "dialogue_lane": dlg.get("dialogue_lane"),
        "planner_reason": dlg.get("planner_reason"),
        "plan_modules": [
            s.get("module") for s in (plan_snap.get("steps") or []) if s.get("module")
        ],
    }


def build_turn_chain(
    *,
    case_id: str,
    user_text: str,
    user_id: str,
    reply: str,
    case: Dict[str, Any],
    plan: Any,
    elapsed_ms: int,
    t0: float,
    group_id: Optional[str] = None,
) -> Dict[str, Any]:
    val_errors = validate_reply(reply, user_text, case)
    reply_leaks = scan_text_leaks(reply, role="assistant")
    user_leaks = scan_text_leaks(user_text, role="user")
    chain = {
        "ts": _now_iso(),
        "id": case_id,
        "user_id": user_id,
        "user_text": user_text[:300],
        "reply_preview": (reply or "")[:500],
        "elapsed_ms": elapsed_ms,
        "route": route_snapshot(user_text),
        "trajectory": trajectory_summary(user_id, user_text, plan, group_id),
        "plan": plan_snapshot(plan),
        "after_execute": dialogue_snapshot(user_id, group_id),
        "validators": val_errors,
        "pass": not val_errors and not reply_leaks,
        "quality": quality_snapshot(user_text, reply, user_id),
        "leaks": {"reply": reply_leaks, "user": user_leaks},
        "llm_calls": llm_calls_since(t0),
    }
    if reply_leaks:
        chain["pass"] = False
        chain["errors"] = list(val_errors)
        for lk in reply_leaks:
            chain["errors"].append(f"leak:{lk.get('code')}")
    elif val_errors:
        chain["errors"] = list(val_errors)
    return chain
