"""
Autopilot preset for zero-touch operation.

Включается через GEMMA_AUTOPILOT_MODE=on (или true/1/yes).
Подставляет только отсутствующие переменные (если os.getenv(k) is None),
не перетирая явные значения из .env.

Пакет «сам подстраивается»: короткий LLM-контур задачи, цепочка второго tool,
подсказки маршрутизации инструментов — см. defaults ниже.
"""
from __future__ import annotations

import os
from typing import Dict


def _truthy(v: str) -> bool:
    return (v or "").strip().lower() in {"1", "true", "yes", "on"}


def autopilot_enabled() -> bool:
    return _truthy(os.getenv("GEMMA_AUTOPILOT_MODE", ""))


def apply_autopilot_defaults() -> Dict[str, str]:
    """
    Apply recommended defaults for stable production diagnostics.
    Returns keys that were actually set by autopilot.
    """
    if not autopilot_enabled():
        return {}

    defaults = {
        # Диалог ближе к «отточенному» боту: меньше лишних инструментов, маршрут намерения в промпте.
        "BRAIN_TOOLS_MODE": "auto",
        "BRAIN_EXTENSION_TOOLS": "true",
        "PROMPT_INTENT_ROUTING": "true",
        "PERSONA_PREPEND_MODE": "off",
        # Экономичный autopilot: меньше дополнительных LLM-стадий по умолчанию.
        "STRATEGY_LLM_OUTLINE_ENABLED": "false",
        "STRATEGY_LLM_OUTLINE_MIN_CHARS": "280",
        "STRATEGY_LLM_OUTLINE_ALWAYS": "false",
        "STRATEGY_LLM_OUTLINE_MAX_TOKENS": "320",
        # Мета-намерение (обратная связь / разбор переписки / сброс сюжета) — короткий JSON-вызов до мозга.
        "META_INTENT_PROBE_ENABLED": "false",
        "META_INTENT_MIN_CONFIDENCE": "0.5",
        "META_INTENT_MAX_TOKENS": "120",
        "BRAIN_TOOL_CHAIN_MAX": "0",
        "BRAIN_TOOL_ROUTING_HINT": "false",
        "BRAIN_TOOLS_PRIORITIZE_HINT": "false",
        "LOOKAHEAD_PLANNER_ENABLED": "false",
        # Логи по умолчанию тише (меньше CPU/диск). Полная телеметрия: GEMMA_CORE_LOG_FULL=true,
        # LATENCY_TRACE_LOG=all, GEMMA_LLM_AUDIT_LOG=true — задайте явно в .env при отладке.
        "LOG_FORMAT": "plain",
        "LATENCY_TRACE_LOG": "slow",
        "LATENCY_TRACE_SLOW_MS": "1800",
        "LIVE_PULSE_PLANNER_TAIL": "48",
        # warn 60 с — рентген; critical 90 с — «критично» для эскалации/автопилота high.
        "LIVE_PULSE_TELEGRAM_P95_WARN_MS": "60000",
        "LIVE_PULSE_TELEGRAM_P95_CRITICAL_MS": "90000",
        "LIVE_PULSE_OPENROUTER_P95_CRITICAL_MS": "12000",
        # Keep maintenance less noisy under load.
        "SELF_MAINTENANCE_INTERVAL_SEC": "1200",
        # Conservative safety defaults.
        "RESILIENCE_ERROR_COUNT_SEVERITIES": "error",
        "SECURITY_JOURNAL_WARNINGS": "false",
        # Practical network defaults for flaky links.
        "CONNECTIVITY_CHECK_TIMEOUT_SEC": "25",
        "TELEGRAM_HTTP_TIMEOUT": "240",
        "TELEGRAM_GET_ME_TIMEOUT_SEC": "25",
        # Дайджест использования и тихий LLM-probe (подробнее .env.example)
        "AUTOPILOT_DIGEST_HOURS_UTC": "8,20",
        "AUTOPILOT_INNER_TICK_SEC": "60",
        "AUTOPILOT_IDLE_MIN_SEC": "900",
        "AUTOPILOT_QUIET_HOURS_UTC": "0,1,2,3,4,5,22,23",
        "AUTOPILOT_LLM_PROBE_MIN_INTERVAL_SEC": "7200",
    }

    applied: Dict[str, str] = {}
    for k, v in defaults.items():
        if os.getenv(k) is None:
            os.environ[k] = v
            applied[k] = v
    return applied

