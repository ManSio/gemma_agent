"""
«Пульс» системы и хвост решений планировщика — для админ-команды /admin_pulse.

Не пишет на диск; кольцевой буфер в памяти (последние N решений).
"""
from __future__ import annotations

import logging

import os
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional

_MONITOR_KEYS = (
    "input_messages_total",
    "plan_calls",
    "planner_decisions_total",
    "planner_fallback_total",
    "execute_plan_calls",
    "trace_finished_total",
    "openrouter_completion_ok_total",
    "openrouter_completion_fail_total",
    "openrouter_prompt_tokens_total",
    "openrouter_completion_tokens_total",
    "openrouter_paid_completions_total",
    "maintenance_cycles_total",
    "flood_blocked_total",
    "security_high_risk_total",
    "math_generic_help_reply_total",
    "user_facts_confirmation_prompt_total",
    "user_facts_confirmation_prompt_city_total",
    "user_facts_confirmation_prompt_currency_total",
    "anti_intrusion_guard_trigger_total",
    "anti_intrusion_guard_silent_skip_total",
    "image_gen_nl_intent_total",
    "admin_bug_nl_phrase_total",
)

logger = logging.getLogger(__name__)

def _tail_max_from_env() -> int:
    raw = os.getenv("LIVE_PULSE_PLANNER_TAIL", "24")
    try:
        n = int((raw or "24").strip())
    except ValueError:
        n = 24
    return max(8, min(96, n))


_tail_max = _tail_max_from_env()
_planner_tail: Deque[Dict[str, Any]] = deque(maxlen=_tail_max)
_lock = threading.Lock()


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(str(raw).strip())
    except ValueError:
        return default


def p95_anomaly_thresholds_ms() -> Dict[str, float]:
    """
    Пороги для /admin_xray и автопилота (аномалии по p95).
    Поднять, если длинные ответы (20–25 с) для вас норма.
    """
    # Полный ход (brain+tools+Telegram) у владельца часто 20–60 с — не считать критикой на 8 с.
    tg_warn = _env_float("LIVE_PULSE_TELEGRAM_P95_WARN_MS", 18000.0)
    tg_crit = _env_float("LIVE_PULSE_TELEGRAM_P95_CRITICAL_MS", 90000.0)
    or_crit = _env_float("LIVE_PULSE_OPENROUTER_P95_CRITICAL_MS", 12000.0)
    if tg_crit < tg_warn:
        tg_crit = tg_warn
    return {
        "telegram_warn_ms": tg_warn,
        "telegram_critical_ms": tg_crit,
        "openrouter_critical_ms": max(0.0, or_crit),
    }


def record_planner_pulse(
    *,
    intent: str,
    module: str,
    fallback: bool,
    reason: str,
    skill_name: str,
    trace_id: str,
    maintenance_ran: bool,
    safe_mode: bool,
) -> None:
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "intent": intent,
        "module": module,
        "fallback": fallback,
        "reason": reason,
        "skill_name": skill_name or None,
        "trace_id": (trace_id[:12] or None) if trace_id else None,
        "maintenance_ran": maintenance_ran,
        "safe_mode": safe_mode,
    }
    with _lock:
        _planner_tail.append(row)


def recent_planner_tail() -> List[Dict[str, Any]]:
    with _lock:
        return list(_planner_tail)


def build_pulse_snapshot(orchestrator: Optional[Any] = None) -> Dict[str, Any]:
    """Сводка для «рентгена»: метрики, p95, воркер, хост, boot, resilience, последние решения."""
    from core.boot_timeline import boot_timeline_snapshot
    from core.host_resources import get_host_resource_snapshot
    from core.monitoring import MONITOR
    from core.observability import OBS
    from core.task_worker import WORKER

    cnt = MONITOR.counters
    monitoring_pick = {k: int(cnt.get(k, 0)) for k in _MONITOR_KEYS}

    resilience: Dict[str, Any] = {}
    if orchestrator is not None and hasattr(orchestrator, "_resilience"):
        rc = getattr(orchestrator, "_resilience")
        try:
            resilience = {
                "enabled": rc.is_enabled(),
                "safe_mode_active": rc.is_safe_mode() if rc.is_enabled() else False,
            }
        except Exception as e:
            resilience = {"error": str(e)}

    boot = boot_timeline_snapshot()
    marks = boot.get("marks") or []
    last_mark = marks[-1] if marks else {}

    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "label": "live_pulse",
        "monitoring": monitoring_pick,
        "monitoring_distinct_keys": len(cnt),
        "observability": {
            "active_traces": OBS.snapshot().get("active_traces", 0),
            "p95_ms": {
                "telegram_pipeline": int(round(OBS.p95("telegram_pipeline"))),
                "openrouter_completion": int(round(OBS.p95("openrouter_completion_ms"))),
            },
        },
        "heavy_worker": WORKER.snapshot(),
        "host_resources": get_host_resource_snapshot(),
        "boot": {
            "origin_utc": boot.get("origin_utc"),
            "last_mark_name": last_mark.get("name"),
            "last_mark_delta_ms": last_mark.get("delta_ms"),
            "plugin_count_at_ready": last_mark.get("plugin_count"),
        },
        "resilience": resilience,
        "planner_recent": recent_planner_tail(),
        "healers": _healers_snapshot_safe(),
    }


def _detect_anomalies(snap: Dict[str, Any]) -> List[Dict[str, Any]]:
    anomalies: List[Dict[str, Any]] = []
    thr = p95_anomaly_thresholds_ms()
    tg_warn = thr["telegram_warn_ms"]
    tg_crit = thr["telegram_critical_ms"]
    or_crit = thr["openrouter_critical_ms"]
    p95 = ((snap.get("observability") or {}).get("p95_ms") or {}) if isinstance(snap.get("observability"), dict) else {}
    tg_p95 = float(p95.get("telegram_pipeline") or 0.0)
    llm_p95 = float(p95.get("openrouter_completion") or 0.0)
    if tg_crit > 0 and tg_p95 >= tg_crit:
        code = "telegram_p95_very_high"
        sev = "high"
        if or_crit > 0 and llm_p95 > 0 and llm_p95 < or_crit * 0.75:
            code = "telegram_p95_pipeline_slow"
            sev = "warn"
        anomalies.append(
            {
                "severity": sev,
                "code": code,
                "detail": f"telegram_pipeline p95={tg_p95:.0f}ms openrouter_p95={llm_p95:.0f}ms",
            }
        )
    elif tg_warn > 0 and tg_p95 >= tg_warn:
        anomalies.append({"severity": "warn", "code": "telegram_p95_high", "detail": f"telegram_pipeline p95={tg_p95:.0f}ms"})
    if or_crit > 0 and llm_p95 >= or_crit:
        anomalies.append({"severity": "high", "code": "openrouter_p95_very_high", "detail": f"openrouter_completion p95={llm_p95:.0f}ms"})

    mon = snap.get("monitoring") if isinstance(snap.get("monitoring"), dict) else {}
    ok = int(mon.get("openrouter_completion_ok_total") or 0)
    fail = int(mon.get("openrouter_completion_fail_total") or 0)
    total = ok + fail
    if total >= 10:
        fail_ratio = fail / max(1, total)
        if fail_ratio >= 0.35:
            anomalies.append({"severity": "high", "code": "openrouter_fail_ratio_high", "detail": f"LLM fail ratio={fail_ratio:.0%} ({fail}/{total})"})
        elif fail_ratio >= 0.15:
            anomalies.append({"severity": "warn", "code": "openrouter_fail_ratio_warn", "detail": f"LLM fail ratio={fail_ratio:.0%} ({fail}/{total})"})

    # Поведенческие «неуместные» автоподсказки: если часто всплывают, это UX-регрессия.
    turns = int(mon.get("input_messages_total") or 0)
    math_help = int(mon.get("math_generic_help_reply_total") or 0)
    if turns >= 30 and math_help >= 3:
        ratio = math_help / max(1, turns)
        sev = "high" if ratio >= 0.08 else "warn"
        anomalies.append(
            {
                "severity": sev,
                "code": "math_help_reply_spam",
                "detail": f"math generic help replies={math_help}/{turns} ({ratio:.1%})",
            }
        )

    confirm_total = int(mon.get("user_facts_confirmation_prompt_total") or 0)
    confirm_city = int(mon.get("user_facts_confirmation_prompt_city_total") or 0)
    confirm_currency = int(mon.get("user_facts_confirmation_prompt_currency_total") or 0)
    if turns >= 30 and confirm_total >= 5:
        ratio = confirm_total / max(1, turns)
        sev = "high" if ratio >= 0.10 else "warn"
        anomalies.append(
            {
                "severity": sev,
                "code": "facts_confirmation_prompt_spam",
                "detail": f"facts confirmation prompts={confirm_total}/{turns} ({ratio:.1%})",
            }
        )
    if turns >= 30 and confirm_currency >= 2:
        anomalies.append(
            {
                "severity": "warn",
                "code": "facts_currency_prompt_repeated",
                "detail": f"currency confirmation prompts={confirm_currency}/{turns}",
            }
        )
    if turns >= 30 and confirm_city >= 2:
        anomalies.append(
            {
                "severity": "warn",
                "code": "facts_city_prompt_repeated",
                "detail": f"city confirmation prompts={confirm_city}/{turns}",
            }
        )

    hw = snap.get("heavy_worker") if isinstance(snap.get("heavy_worker"), dict) else {}
    qd = int(hw.get("queue_depth") or 0)
    qm = int(hw.get("queue_max") or 0)
    if qm > 0 and qd >= max(2, int(qm * 0.8)):
        anomalies.append({"severity": "high", "code": "worker_queue_near_limit", "detail": f"queue_depth={qd} queue_max={qm}"})

    hr = snap.get("host_resources") if isinstance(snap.get("host_resources"), dict) else {}
    if hr.get("available"):
        pr = hr.get("pressure") if isinstance(hr.get("pressure"), dict) else {}
        level = str(pr.get("level") or "ok")
        if level == "critical":
            anomalies.append({"severity": "high", "code": "host_pressure_critical", "detail": f"host pressure={level}"})
        elif level == "warn":
            anomalies.append({"severity": "warn", "code": "host_pressure_warn", "detail": f"host pressure={level}"})

    res = snap.get("resilience") if isinstance(snap.get("resilience"), dict) else {}
    if bool(res.get("safe_mode_active")):
        anomalies.append({"severity": "warn", "code": "safe_mode_active", "detail": "Resilience safe mode still active"})

    boot = snap.get("boot") if isinstance(snap.get("boot"), dict) else {}
    boot_ms = float(boot.get("last_mark_delta_ms") or 0.0)
    if boot_ms >= 20000:
        anomalies.append({"severity": "warn", "code": "slow_boot_path", "detail": f"last boot mark at +{boot_ms:.0f}ms"})
    return anomalies


def xray_anomalies_for_display(
    xray: Dict[str, Any],
    *,
    include_warn: bool = True,
) -> List[Dict[str, Any]]:
    """
    Аномалии для отчётов автопилота и дайджестов: без эхо EventBus и без дублей по code.
    Строки без detail/label не показываем (иначе «code: None» в Telegram).
    """
    raw = xray.get("anomalies") if isinstance(xray.get("anomalies"), list) else []
    seen: Dict[str, Dict[str, Any]] = {}
    for a in raw:
        if not isinstance(a, dict):
            continue
        if a.get("type") == "event_bus":
            continue
        code = str(a.get("code") or "").strip()
        if not code:
            continue
        detail = a.get("detail")
        label = a.get("label")
        if detail is None and not label:
            continue
        if not include_warn and str(a.get("severity") or "").lower() == "warn":
            continue
        prev = seen.get(code)
        if prev is None or (not prev.get("detail") and detail):
            seen[code] = a
    return list(seen.values())


def _healers_snapshot_safe() -> Dict[str, Any]:
    """Snapshot healers с защитой от ошибок импорта."""
    try:
        from core.event_healers import healers_snapshot
        return healers_snapshot()
    except Exception:
        return {}


def build_xray_snapshot(orchestrator: Optional[Any] = None) -> Dict[str, Any]:
    """Расширенный «рентген»: pulse + ошибки + аномалии."""
    from core.error_analysis import aggregate_error_stats
    from core.usage_learning import insights as usage_insights, snapshot as usage_snapshot

    pulse = build_pulse_snapshot(orchestrator)
    errors = aggregate_error_stats(limit=500)
    xray = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "label": "xray",
        "pulse": pulse,
        "errors": errors,
        "usage_learning": usage_snapshot(),
        "usage_insights": usage_insights(),
    }
    xray["anomalies"] = _detect_anomalies(pulse)
    return xray
