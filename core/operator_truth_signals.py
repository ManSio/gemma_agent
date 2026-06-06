"""
Честные сигналы для диалога: счётчики MONITOR + загрузка чат-модуля, без «всё в норме» вслепую.
Подмешиваются в external_hint мозга при вопросах о здоровье бота / обрывах или при ненулевых проблемах.
"""
from __future__ import annotations

from typing import Any, Dict, FrozenSet

from core.monitoring import MONITOR

_DIALOG_KEYS: FrozenSet[str] = frozenset({"chat-orchestrator", "chat_orchestrator", "smartchat"})


def user_asks_operator_health(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    keys = (
        "диагност",
        "здоровье бот",
        "бот слом",
        "бот лом",
        "не работает бот",
        "модули в норме",
        "модули в порядке",
        "все нормально",
        "всё нормально",
        "все норм",
        "всё норм",
        "обрыв",
        "обрез",
        "не дописыв",
        "fallback",
        "лог бот",
        "admin_health",
        "system_state",
        "/status",
        "статус бот",
        "админ_health",
        "техработ",
        "работоспособн",
        "исправн",
    )
    return any(k in t for k in keys)


def user_reports_truncation(text: str) -> bool:
    t = (text or "").strip().lower()
    return any(x in t for x in ("обрез", "обрыв", "обруб", "не допис", "лимит 4096", "лимит символ"))


def compute_signals(orchestrator: Any) -> Dict[str, Any]:
    c = dict(MONITOR.snapshot().get("counters") or {})
    loaded = set(getattr(orchestrator.plugin_registry, "loaded_modules", {}).keys())
    dialog_ok = bool(loaded & _DIALOG_KEYS)
    rc = getattr(orchestrator, "_resilience", None)
    safe = bool(rc.is_safe_mode()) if rc is not None and hasattr(rc, "is_safe_mode") else False
    fb = int(c.get("planner_fallback_total", 0))
    sus = int(c.get("telegram_reply_suspect_incomplete_total", 0))
    issues: list[str] = []
    if fb > 0:
        issues.append("planner_fallback")
    if sus > 0:
        issues.append("reply_suspect_truncation")
    if not dialog_ok:
        issues.append("no_dialog_module")
    if safe:
        issues.append("safe_mode")
    return {
        "planner_fallback_total": fb,
        "telegram_reply_suspect_incomplete_total": sus,
        "execute_plan_calls": int(c.get("execute_plan_calls", 0)),
        "chat_dialog_module_loaded": dialog_ok,
        "safe_mode_active": safe,
        "issues": issues,
    }


def format_truth_signals_hint(signals: Dict[str, Any]) -> str:
    lines = [
        "OPERATOR_TRUTH_SIGNALS (факты процесса; не придумывай другие цифры; не отвечай «всё идеально», если ниже есть проблемы):",
        f"- planner_fallback_total={signals['planner_fallback_total']} (запросы ушли в __fallback__ / не в чат-модуль)",
        f"- telegram_reply_suspect_incomplete_total={signals['telegram_reply_suspect_incomplete_total']} (эвристика обрыва ответа; смотри лог [turn] suspect_incomplete)",
        f"- execute_plan_calls={signals['execute_plan_calls']}",
        f"- chat_dialog_module_loaded={signals['chat_dialog_module_loaded']}",
        f"- safe_mode_active={signals['safe_mode_active']}",
    ]
    if signals.get("issues"):
        lines.append(
            "Проблемные флаги: "
            + ", ".join(signals["issues"])
            + ". Перечисли их пользователю и предложи: /admin_health, /admin_connectivity, лог "
            "data/logs/gemma_bot.log (строки [turn] и WARNING)."
        )
    else:
        lines.append(
            "Явных красных флагов по счётчикам с момента старта нет. Если пользователь описал симптом — не отмахивайся; "
            "предложи конкретную проверку (/admin_health_json, последние логи)."
        )
    return "\n".join(lines)


def maybe_attach_operator_truth_signals(
    ctx: Dict[str, Any],
    *,
    orchestrator: Any,
    user_text: str,
    is_admin: bool,
) -> None:
    signals = compute_signals(orchestrator)
    ut = user_text or ""
    inject = False
    if is_admin and (user_asks_operator_health(ut) or bool(signals.get("issues"))):
        inject = True
    if not is_admin and user_reports_truncation(ut):
        inject = True
    if not inject:
        return
    ctx["operator_truth_signals"] = signals
    ctx["operator_truth_signals_hint"] = format_truth_signals_hint(signals)
