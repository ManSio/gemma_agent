from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from core.llm_usage_store import load_records
from core.report_timezone import get_report_tz


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int((os.getenv(name) or "").strip() or str(default)))
    except (TypeError, ValueError):
        return default


def _parse_float(name: str, default: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    try:
        val = float((os.getenv(name) or "").strip() or str(default))
    except (TypeError, ValueError):
        val = default
    return max(minimum, min(maximum, val))


def _parse_budget_float(name: str, default: float, minimum: float = 0.0, maximum: float = 100000.0) -> float:
    try:
        val = float((os.getenv(name) or "").strip() or str(default))
    except (TypeError, ValueError):
        val = default
    return max(minimum, min(maximum, val))


def cost_autopilot_enabled() -> bool:
    return _truthy("COST_AUTOPILOT_ENABLED", False)


def _parse_ts_local_day(ts: Any) -> str:
    if not ts:
        return ""
    try:
        s = str(ts).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(get_report_tz()).date().isoformat()
    except Exception:
        return ""


def _today_tokens_spent(max_rows: int = 20000) -> int:
    rows = load_records(max_lines=max_rows)
    if not rows:
        return 0
    today_local = datetime.now(timezone.utc).astimezone(get_report_tz()).date().isoformat()
    total = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        if not row.get("ok"):
            continue
        if _parse_ts_local_day(row.get("ts")) != today_local:
            continue
        try:
            tt = int(row.get("total_tokens") or 0)
            pt = int(row.get("prompt_tokens") or 0)
            ct = int(row.get("completion_tokens") or 0)
        except (TypeError, ValueError):
            continue
        total += tt if tt > 0 else max(0, pt + ct)
    return max(0, total)


def _today_cost_spent_usd(max_rows: int = 20000) -> float:
    rows = load_records(max_lines=max_rows)
    if not rows:
        return 0.0
    today_local = datetime.now(timezone.utc).astimezone(get_report_tz()).date().isoformat()
    total = 0.0
    for row in rows:
        if not isinstance(row, dict) or not row.get("ok"):
            continue
        if _parse_ts_local_day(row.get("ts")) != today_local:
            continue
        try:
            total += float(row.get("cost") or 0.0)
        except (TypeError, ValueError):
            continue
    return max(0.0, total)


def daily_cost_budget_usd() -> float:
    """Hard daily LLM spend cap in USD from .env."""
    return _parse_budget_float("COST_DAILY_USD_BUDGET", 10.0, minimum=0.01, maximum=10000.0)


def llm_daily_cost_blocked_reason() -> Optional[str]:
    """Return block reason when today's LLM spend reached COST_DAILY_USD_BUDGET."""
    if not _truthy("COST_DAILY_USD_HARD_STOP", True):
        return None
    budget = daily_cost_budget_usd()
    spent = _today_cost_spent_usd(max_rows=_parse_int("COST_LLM_USAGE_MAX_ROWS", 20000, minimum=200))
    if spent >= budget:
        return f"Daily LLM cost budget exceeded ({spent:.4f} >= {budget:.4f} USD)"
    return None


def _is_rich_request(
    *,
    user_text: str,
    planned_intent: str,
    has_rich_context: bool,
    predictive_hint: Optional[Dict[str, Any]],
) -> bool:
    txt = (user_text or "").strip()
    intent = (planned_intent or "").strip().lower()
    if has_rich_context:
        return True
    if intent not in {"general", "empty"}:
        return True
    if len(txt) >= 420:
        return True
    ph = predictive_hint if isinstance(predictive_hint, dict) else {}
    try:
        conf = float(ph.get("confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    return conf >= 0.82


def build_cost_autopilot_patch(
    *,
    user_text: str,
    planned_intent: str,
    planned_module: str,
    predictive_hint: Optional[Dict[str, Any]] = None,
    has_rich_context: bool = False,
) -> Dict[str, Any]:
    if not cost_autopilot_enabled():
        return {}
    budget = _parse_int("COST_DAILY_TOKEN_BUDGET", 300000, minimum=1)
    hard_thr = _parse_float("COST_HARD_SAVING_THRESHOLD", 0.9, minimum=0.4, maximum=1.0)
    saving_thr = _parse_float("COST_SAVING_THRESHOLD", 0.75, minimum=0.2, maximum=hard_thr)
    today_tokens = _today_tokens_spent(max_rows=_parse_int("COST_LLM_USAGE_MAX_ROWS", 20000, minimum=200))
    ratio = (float(today_tokens) / float(budget)) if budget > 0 else 0.0
    if ratio >= hard_thr:
        mode = "hard_saving"
    elif ratio >= saving_thr:
        mode = "saving"
    else:
        mode = "normal"
    rich = _is_rich_request(
        user_text=user_text,
        planned_intent=planned_intent,
        has_rich_context=has_rich_context,
        predictive_hint=predictive_hint,
    )
    txt_len = len((user_text or "").strip())
    short_general = (planned_intent or "").strip().lower() == "general" and txt_len <= 220 and not rich

    patch: Dict[str, Any] = {
        "mode": mode,
        "today_tokens": today_tokens,
        "daily_budget": budget,
        "usage_ratio": round(ratio, 4),
    }
    if mode in {"saving", "hard_saving"} and not rich:
        patch["force_verbosity"] = "concise"
    if short_general and mode == "saving":
        patch["task_tier_ceiling"] = "nested"
    if short_general and mode == "hard_saving":
        patch["task_tier_ceiling"] = "shallow"
    if mode == "saving":
        patch["disable_strategy_hint"] = True
    if mode == "hard_saving":
        patch["disable_strategy_hint"] = True
        patch["disable_experience_hint"] = True
        patch["disable_route_risk_hint"] = True
        patch["disable_tools"] = not rich and planned_module in {"chat-orchestrator", "chat_orchestrator", "smartchat"}
    return patch
