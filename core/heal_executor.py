"""
Heal Executor — реальное применение шагов лечения из рекомендаций LLM Triage.

Поддерживаемые шаги:
  /admin_plugin_disable <name>    → PluginRegistry.disable_module()
  /admin_plugin_enable <name>     → PluginRegistry.enable_module()
  env KEY=VALUE                   → os.environ[key] = value (runtime)
  ephemeral patch: <trigger> || <instruction>  → EphemeralLessons.add_lesson()
  reset module failures <name>    → ModuleFailureHealer.reset(name)
  restart container               → ResilienceController.request_container_restart()
  reset error counters            → log_tool_error counters reset

Каждый шаг исполняется изолированно, ошибка одного не ломает остальные.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from core.event_bus import bus

logger = logging.getLogger(__name__)

# Разрешённые env-переменные (белый список — нельзя менять токены и секреты)
_ALLOWED_ENV_KEYS: frozenset = frozenset({
    "HEALER_MODULE_MAX_FAILURES",
    "HEALER_ANOMALY_WINDOW_SEC",
    "HEALER_ANOMALY_MAX_COUNT",
    "LLM_TRIAGE_ENABLED",
    "LLM_TRIAGE_AUTOFLUSH_COUNT",
    "LLM_TRIAGE_MAX_CONTEXT_CHARS",
    "MODEL_SWITCH_THRESHOLD",
    "EVENT_BUS_HISTORY_SIZE",
    "RESILIENCE_SAFE_ERROR_TOTAL",
    "RESILIENCE_RECOVERY_OK_CYCLES",
    "DIALOGUE_MEMORY_MAX",
    "CONTEXT_RECENT_KEEP_MESSAGES",
})

# ── Парсинг шагов ────────────────────────────────────────────────────────


def parse_steps(steps: List[str]) -> List[Dict[str, Any]]:
    """Преобразовать текстовые шаги в структурированные команды."""
    parsed: List[Dict[str, Any]] = []
    for raw in steps:
        step = raw.strip()
        if not step:
            continue
        cmd = _parse_single_step(step)
        if cmd:
            cmd["_raw"] = step
            parsed.append(cmd)
        else:
            parsed.append({
                "action": "unknown",
                "_raw": step,
                "error": "unrecognized step format",
            })
    return parsed


def _parse_single_step(step: str) -> Optional[Dict[str, Any]]:
    """Распарсить один шаг."""
    step_lower = step.lower().strip()

    # /admin_plugin_disable <name>
    m = re.match(r"^/admin_plugin_disable\s+(\S+)", step_lower)
    if m:
        return {"action": "disable_module", "module": m.group(1)}

    # /admin_plugin_enable <name>
    m = re.match(r"^/admin_plugin_enable\s+(\S+)", step_lower)
    if m:
        return {"action": "enable_module", "module": m.group(1)}

    # disable module <name>
    m = re.match(r"^disable\s+module\s+(\S+)", step_lower)
    if m:
        return {"action": "disable_module", "module": m.group(1)}

    # enable module <name>
    m = re.match(r"^enable\s+module\s+(\S+)", step_lower)
    if m:
        return {"action": "enable_module", "module": m.group(1)}

    # env KEY=VALUE
    m = re.match(r"^env\s+(\w+)=(.+)", step)
    if m:
        key = m.group(1).strip()
        val = m.group(2).strip()
        if key in _ALLOWED_ENV_KEYS:
            return {"action": "set_env", "key": key, "value": val}
        else:
            return {"action": "set_env_blocked", "key": key, "error": f"key {key} not in allowlist"}

    # reset module failures <name>
    m = re.match(r"^reset\s+module\s+failures\s+(\S+)", step_lower)
    if m:
        return {"action": "reset_module_failures", "module": m.group(1)}

    # reset error counters
    if step_lower in ("reset error counters", "reset error counter"):
        return {"action": "reset_error_counters"}

    # restart container
    if step_lower in ("restart container", "restart"):
        return {"action": "restart_container"}

    # ephemeral patch: <trigger> || <instruction>
    m = re.match(r"^ephemeral\s+patch:\s*(.+?)\s*\|\|\s*(.+)", step, re.DOTALL)
    if m:
        return {
            "action": "create_ephemeral_patch",
            "trigger": m.group(1).strip(),
            "instruction": m.group(2).strip(),
        }

    # clear safe mode
    if "clear safe mode" in step_lower or "exit safe mode" in step_lower:
        return {"action": "exit_safe_mode"}

    return None


# ── Исполнение ──────────────────────────────────────────────────────────


# Приоритеты действий (меньше = важнее)
_ACTION_PRIORITY: Dict[str, int] = {
    "restart_container": 0,
    "exit_safe_mode": 1,
    "disable_module": 2,
    "set_env": 3,
    "enable_module": 4,
    "reset_module_failures": 5,
    "reset_error_counters": 6,
    "create_ephemeral_patch": 7,
}


async def apply_steps(
    steps: List[str],
    *,
    reason: str = "llm_triage",
) -> Dict[str, Any]:
    """
    Применить список шагов лечения.

    Шаги сортируются по приоритету: restart > safe_mode > disable > env > enable > reset > patch.
    Критичные шаги выполняются первыми.

    Возвращает:
      ok: True если все шаги успешны
      results: список результатов каждого шага
      summary: текст для админа
    """
    parsed = parse_steps(steps)
    # Сортировка по приоритету
    parsed.sort(key=lambda c: _ACTION_PRIORITY.get(c.get("action", ""), 99))
    results: List[Dict[str, Any]] = []
    all_ok = True

    for cmd in parsed:
        action = cmd.get("action", "unknown")
        try:
            if action == "disable_module":
                result = await _exec_disable_module(cmd["module"])
            elif action == "enable_module":
                result = await _exec_enable_module(cmd["module"])
            elif action == "set_env":
                result = _exec_set_env(cmd["key"], cmd["value"])
            elif action == "set_env_blocked":
                result = {"ok": False, "error": cmd.get("error", "blocked")}
                all_ok = False
            elif action == "reset_module_failures":
                result = _exec_reset_module_failures(cmd["module"])
            elif action == "reset_error_counters":
                result = _exec_reset_error_counters()
            elif action == "restart_container":
                result = _exec_restart_container(reason)
            elif action == "create_ephemeral_patch":
                result = await _exec_create_ephemeral_patch(cmd["trigger"], cmd["instruction"])
            elif action == "exit_safe_mode":
                result = _exec_exit_safe_mode()
            else:
                result = {"ok": False, "error": f"unknown action: {action}"}
                all_ok = False
        except Exception as exc:
            result = {"ok": False, "error": str(exc)[:200]}
            all_ok = False

        result["action"] = action
        result["_raw"] = cmd.get("_raw", "")
        results.append(result)

    summary = _build_summary(results)
    bus.emit("healer.action", {
        "healer": "HealExecutor",
        "action": "apply_steps",
        "reason": reason,
        "details": {
            "total": len(results),
            "ok": sum(1 for r in results if r.get("ok")),
            "fail": sum(1 for r in results if not r.get("ok")),
            "summary": summary,
        },
    })

    return {
        "ok": all_ok,
        "results": results,
        "summary": summary,
    }


def _build_summary(results: List[Dict[str, Any]]) -> str:
    ok_n = sum(1 for r in results if r.get("ok"))
    fail_n = len(results) - ok_n
    lines = [f"Шагов: {len(results)}, успешно: {ok_n}, ошибок: {fail_n}"]
    for r in results:
        ok_icon = "✅" if r.get("ok") else "❌"
        action = r.get("action", "?")
        error = r.get("error", "")
        err_suffix = f" — {error[:100]}" if error else ""
        lines.append(f"{ok_icon} {action}{err_suffix}")
    return "\n".join(lines)


# ── Исполнители ─────────────────────────────────────────────────────────


async def _exec_disable_module(name: str) -> Dict[str, Any]:
    try:
        from core.plugin_registry import plugin_registry
        ok = plugin_registry.disable_module(name)
        if ok:
            bus.emit("module.disabled", {"module": name, "reason": "heal_executor"})
        return {"ok": ok, "error": None if ok else f"module {name} not found or failed to disable"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def _exec_enable_module(name: str) -> Dict[str, Any]:
    try:
        from core.plugin_registry import plugin_registry
        ok = plugin_registry.enable_module(name)
        if ok:
            bus.emit("module.enabled", {"module": name, "reason": "heal_executor"})
        return {"ok": ok, "error": None if ok else f"module {name} not found or failed to enable"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _exec_set_env(key: str, value: str) -> Dict[str, Any]:
    try:
        os.environ[key] = value
        logger.info("[heal_executor] set env %s=%s", key, value)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _exec_reset_module_failures(module: str) -> Dict[str, Any]:
    try:
        from core.event_healers import get_module_failure_healer
        healer = get_module_failure_healer()
        healer.reset(module)
        logger.info("[heal_executor] reset module failures: %s", module)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _exec_reset_error_counters() -> Dict[str, Any]:
    try:
        from core.self_healing import reset_error_counters as _do_reset
        _do_reset()
        logger.info("[heal_executor] reset error counters")
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _exec_restart_container(reason: str) -> Dict[str, Any]:
    try:
        from core.resilience_controller import ResilienceController
        rc = ResilienceController()
        rc.request_container_restart(f"heal_executor: {reason[:100]}")
        logger.info("[heal_executor] restart container requested")
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def _exec_create_ephemeral_patch(trigger: str, instruction: str) -> Dict[str, Any]:
    try:
        from core.ephemeral_lessons import add_lesson
        add_lesson(
            trigger=trigger,
            instruction=instruction,
            match_regex=False,
            force_general_when_math_probe=False,
        )
        logger.info("[heal_executor] ephemeral patch: trigger=%s", trigger[:50])
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _exec_exit_safe_mode() -> Dict[str, Any]:
    try:
        from core.resilience_controller import ResilienceController
        rc = ResilienceController()
        if rc.is_safe_mode():
            rc.exit_safe_mode("heal_executor")
            return {"ok": True}
        return {"ok": True, "warn": "safe mode was not active"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
