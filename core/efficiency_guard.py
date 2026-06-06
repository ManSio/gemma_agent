from __future__ import annotations

import os
from typing import Any, Dict, Optional

from core.efficiency_report import build_efficiency_snapshot


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _f(name: str, default: float) -> float:
    try:
        return float((os.getenv(name) or "").strip() or str(default))
    except (TypeError, ValueError):
        return default


def efficiency_guard_enabled() -> bool:
    return _truthy("EFFICIENCY_GUARD_ENABLED", True)


def build_efficiency_guard_patch(*, orchestrator: Optional[Any] = None, days: float = 7.0) -> Dict[str, Any]:
    if not efficiency_guard_enabled():
        return {}
    snap = build_efficiency_snapshot(days=days, orchestrator=orchestrator)
    planner = snap.get("planner") if isinstance(snap.get("planner"), dict) else {}
    plugins = snap.get("plugins") if isinstance(snap.get("plugins"), dict) else {}
    token_saving = snap.get("token_saving") if isinstance(snap.get("token_saving"), dict) else {}

    route_success = float(planner.get("route_success_percent") or 0.0)
    plugin_success = float(plugins.get("exec_success_percent") or 0.0)
    token_eff = float(token_saving.get("efficiency_percent") or 0.0)
    planner_total = int(planner.get("decisions_total") or 0)
    plugin_total = int(plugins.get("exec_total") or 0)
    min_samples = max(1, int(_f("EFF_GUARD_MIN_SAMPLES", 20)))

    # Нулевые % после рестарта = нет данных, не деградация (иначе вечный critical).
    if planner_total < min_samples and plugin_total < min_samples:
        return {
            "level": "ok",
            "metrics": {
                "route_success_percent": route_success,
                "plugin_success_percent": plugin_success,
                "token_efficiency_percent": token_eff,
            },
            "insufficient_data": True,
            "min_samples": min_samples,
        }

    route_warn = _f("EFF_GUARD_ROUTE_SUCCESS_WARN_PCT", 85.0)
    route_crit = _f("EFF_GUARD_ROUTE_SUCCESS_CRIT_PCT", 70.0)
    plugin_warn = _f("EFF_GUARD_PLUGIN_SUCCESS_WARN_PCT", 92.0)
    plugin_crit = _f("EFF_GUARD_PLUGIN_SUCCESS_CRIT_PCT", 80.0)
    token_warn = _f("EFF_GUARD_TOKEN_EFF_WARN_PCT", 12.0)

    level = "ok"
    if route_success < route_crit or plugin_success < plugin_crit:
        level = "critical"
    elif route_success < route_warn or plugin_success < plugin_warn or token_eff < token_warn:
        level = "warn"

    patch: Dict[str, Any] = {
        "level": level,
        "metrics": {
            "route_success_percent": route_success,
            "plugin_success_percent": plugin_success,
            "token_efficiency_percent": token_eff,
        },
    }
    if level == "warn":
        patch["force_verbosity"] = "concise"
        patch["task_tier_ceiling"] = "nested"
        patch["strict_routing_guard"] = True
    elif level == "critical":
        patch["force_verbosity"] = "concise"
        patch["task_tier_ceiling"] = "shallow"
        patch["strict_routing_guard"] = True
        patch["disable_tools_for_general"] = True
    return patch
