"""
Healers — async-подписчики на события шины.

Каждый healer реагирует на конкретный event_type:
  | Событие                | Healer                | Действие |
  |------------------------|-----------------------|----------|
  | module.failed          | ModuleFailureHealer   | Счётчик → N=3 эфемерный патч, N=5 disable |
  | bug_report.collected   | BugContextGatherer    | Сбор метрик для диагностики |
  | anomaly.detected       | AnomalyEscalator      | Проверка safe mode |
  | maintenance.tick       | MaintenanceBridge     | Передача в self_healing |
  | openrouter.done        | AutoLatencyHealer     | P95 → setenv MODEL_SWITCH_THRESHOLD |
  | openrouter.done        | AutoFailRatioHealer   | Fail ratio → disable module |
  | maintenance.tick       | AutoHostPressureHealer| Resource pressure → anomaly |
"""
from __future__ import annotations

import logging
import os
import time
from collections import defaultdict, deque
from typing import Any, Deque, Dict, List, Optional

from core.event_bus import bus

logger = logging.getLogger(__name__)


# ─── ModuleFailureHealer ─────────────────────────────────────────────────

class ModuleFailureHealer:
    """
    Слушает module.failed → считает падения модуля.
    - При N=3 → эфемерный патч (LLM не вызывает модуль)
    - При N=5 → auto-disable модуля через HealExecutor
    """

    def __init__(self, max_failures: int = 3) -> None:
        self._failures: Dict[str, int] = defaultdict(int)
        self._max_failures = max(int(os.getenv("HEALER_MODULE_MAX_FAILURES", "3")), 1)
        self._auto_disable_at = int(os.getenv("HEALER_MODULE_AUTO_DISABLE_AT", "5"))
        self._patches_created: set[str] = set()
        self._disabled: Dict[str, float] = {}  # module_name → ts_disabled
        self._re_enable_after_sec = float(os.getenv("HEALER_MODULE_RE_ENABLE_AFTER_SEC", "1800"))  # 30 мин

    async def __call__(self, payload: Dict[str, Any]) -> None:
        module_name = (payload.get("module_name") or "").strip()
        if not module_name:
            return
        self._failures[module_name] += 1
        cnt = self._failures[module_name]

        # Сброс при успехе
        if payload.get("ok"):
            self._failures.pop(module_name, None)
            self._patches_created.discard(module_name)
            self._disabled.pop(module_name, None)
            return

        # Auto-disable при N >= auto_disable_at
        if cnt >= self._auto_disable_at and module_name not in self._disabled:
            self._disabled[module_name] = time.time()
            await self._auto_disable(module_name, cnt)
            return

        # Эфемерный патч при max_failures (если ещё не создали)
        if cnt >= self._max_failures and module_name not in self._patches_created:
            self._patches_created.add(module_name)
            await self._create_patch(module_name, cnt)

    def _check_re_enable(self) -> None:
        """Перепроверить disabled модули — если прошло _re_enable_after_sec, попробовать включить.

        Вызывается из maintenance.tick.
        """
        now = time.time()
        for module_name, ts in list(self._disabled.items()):
            if now - ts < self._re_enable_after_sec:
                continue
            try:
                from core.plugin_registry import plugin_registry
                ok = plugin_registry.enable_module(module_name)
                if ok:
                    self._disabled.pop(module_name, None)
                    self._failures.pop(module_name, None)
                    logger.info("[healer] auto re-enabled module=%s (was disabled for %.0f sec)", module_name, now - ts)
                    from core.event_bus import bus
                    bus.emit("module.enabled", {"module": module_name, "reason": "auto_re_enable"})
                    bus.emit("healer.action", {
                        "healer": "ModuleFailureHealer",
                        "action": "auto_re_enable",
                        "reason": f"cooldown expired for {module_name}",
                        "details": {"module": module_name, "disabled_sec": round(now - ts, 1)},
                    })
                else:
                    # Не удалось включить — продлеваем таймер (попробуем ещё через cooldown)
                    self._disabled[module_name] = now
                    logger.debug("[healer] re-enable failed for %s, will retry later", module_name)
            except Exception as e:
                logger.debug("[healer] re-enable error for %s: %s", module_name, e)
                self._disabled[module_name] = now

    async def _create_patch(self, module_name: str, failures: int) -> None:
        try:
            from core.ephemeral_lessons import add_lesson

            add_lesson(
                trigger=f"{module_name}",
                instruction=(
                    f"Модуль {module_name} упал {failures} раз. "
                    f"Не используй {module_name} — ответь сам или предложи альтернативу."
                ),
                match_regex=False,
                force_general_when_math_probe=False,
            )
            logger.info("[healer] ephemeral patch for module=%s (failures=%d)", module_name, failures)
            # Undo-лог: эфемерные патчи — просто подтверждаем, не откатываем
            try:
                from core.auto_rollback import get_undo_log
                get_undo_log().add(
                    healer="ModuleFailureHealer",
                    action="create_ephemeral_patch",
                    params={"module": module_name, "failures": failures},
                    verify_window_sec=600.0,  # 10 минут — потом auto-confirm
                )
            except Exception as e:
                logger.debug('%s optional failed: %s', 'event_healers', e, exc_info=True)
            bus.emit("healer.action", {
                "healer": "ModuleFailureHealer",
                "action": "create_ephemeral_patch",
                "reason": f"module {module_name} failed {failures} times",
                "details": {"module": module_name, "failures": failures},
            })
        except Exception as e:
            logger.warning("[healer] failed to create patch for %s: %s", module_name, e)

    async def _auto_disable(self, module_name: str, failures: int) -> None:
        try:
            from core.heal_executor import apply_steps

            result = await apply_steps(
                [f"/admin_plugin_disable {module_name}"],
                reason=f"auto_heal: {module_name} failed {failures} times",
            )
            logger.info(
                "[healer] auto-disabled module=%s (failures=%d) ok=%s",
                module_name, failures, result.get("ok"),
            )
            # Undo-лог для auto-disable
            from core.monitoring import MONITOR
            old_total_fail = int(MONITOR.counters.get("module_exec_fail_total", 0))
            try:
                from core.auto_rollback import get_undo_log
                get_undo_log().add(
                    healer="ModuleFailureHealer",
                    action="auto_disable_module",
                    params={
                        "module": module_name,
                        "failures": failures,
                        "old_total_fail": old_total_fail,
                    },
                    verify_window_sec=300.0,  # 5 минут на проверку
                )
            except Exception as e:
                logger.debug('%s optional failed: %s', 'event_healers', e, exc_info=True)
            bus.emit("healer.action", {
                "healer": "ModuleFailureHealer",
                "action": "auto_disable_module",
                "reason": f"module {module_name} failed {failures} times >= {self._auto_disable_at}",
                "details": {"module": module_name, "failures": failures, "ok": result.get("ok")},
            })
        except Exception as e:
            logger.warning("[healer] auto-disable failed for %s: %s", module_name, e)

    def reset(self, module_name: Optional[str] = None) -> None:
        if module_name:
            self._failures.pop(module_name, None)
            self._patches_created.discard(module_name)
            self._disabled.pop(module_name, None)
        else:
            self._failures.clear()
            self._patches_created.clear()
            self._disabled.clear()

    def snapshot(self) -> Dict[str, Any]:
        return {
            "failures": dict(self._failures),
            "patches_created": sorted(self._patches_created),
            "disabled": sorted(self._disabled),
            "re_enable_after_sec": self._re_enable_after_sec,
            "auto_disable_at": self._auto_disable_at,
        }


# ─── BugContextGatherer ──────────────────────────────────────────────────

class BugContextGatherer:
    """
    Слушает bug_report.collected → собирает снимок метрик и
    прикрепляет диагностический контекст к событию (через data).
    """

    async def __call__(self, payload: Dict[str, Any]) -> None:
        try:
            ctx = await self._collect_context()
            payload["diagnostic_context"] = ctx
            logger.info("[healer] BugContextGatherer: attached diagnostic context")
        except Exception as e:
            logger.debug("[healer] BugContextGatherer error: %s", e)

    @staticmethod
    async def _collect_context() -> Dict[str, Any]:
        ctx: Dict[str, Any] = {}

        try:
            recent = bus.history(n=30)
            ctx["recent_events_summary"] = [
                {"event_type": e.event_type, "ts": e.data.get("ts", "")[:19],
                 "correlation_id": e.correlation_id}
                for e in recent
            ]
        except Exception:
            ctx["recent_events_summary"] = []

        try:
            from core.monitoring import MONITOR
            mon = MONITOR.snapshot()
            counters = mon.get("counters", {})
            if isinstance(counters, dict):
                keys = [
                    "input_messages_total", "openrouter_completion_ok_total",
                    "openrouter_completion_fail_total", "openrouter_cost_credits_nanos_total",
                    "module_exec_ok_total", "module_exec_fail_total", "context_collapse_total",
                ]
                ctx["monitor_key_counters"] = {k: counters.get(k, 0) for k in keys}
        except Exception:
            ctx["monitor_key_counters"] = {}

        try:
            from core.observability import OBS
            lats = getattr(OBS, "latencies_ms", {})
            if isinstance(lats, dict):
                ctx["latency_p95"] = {
                    k: v.get("p95") if isinstance(v, dict) else None
                    for k, v in lats.items()
                }
        except Exception:
            ctx["latency_p95"] = {}

        try:
            from core.llm_telemetry import recent_calls_summary
            ctx["llm_recent"] = recent_calls_summary(minutes=5)
        except Exception:
            ctx["llm_recent"] = {}

        return ctx


# ─── AnomalyEscalator ────────────────────────────────────────────────────

class AnomalyEscalator:
    """Слушает anomaly.detected → при высокой частоте → safe mode."""

    _HOST_PRESSURE_CODES = frozenset(
        {"host_pressure", "host_pressure_critical", "host_pressure_warn"}
    )

    def __init__(self) -> None:
        self._recent: Dict[str, list[float]] = defaultdict(list)
        self._window_sec = int(os.getenv("HEALER_ANOMALY_WINDOW_SEC", "600"))
        self._max_anomalies = int(os.getenv("HEALER_ANOMALY_MAX_COUNT", "8"))
        self._reentry_cooldown_sec = float(os.getenv("HEALER_SAFE_MODE_REENTRY_COOLDOWN_SEC", "120"))
        self._last_safe_mode_exit = 0.0

    async def _on_safe_mode_cleared(self, _payload: Dict[str, Any]) -> None:
        """Сброс накопленных аномалий после выхода из safe mode."""
        self._recent.clear()
        self._last_safe_mode_exit = time.time()
        logger.info("[healer] AnomalyEscalator: counters cleared on safe_mode_cleared")

    def _skip_host_pressure_noise(self, code: str) -> bool:
        if code not in self._HOST_PRESSURE_CODES:
            return False
        try:
            from core.host_resources import host_pressure_level

            return host_pressure_level() == "ok"
        except Exception:
            return False

    async def __call__(self, payload: Dict[str, Any]) -> None:
        code = (payload.get("code") or "unknown").strip()
        severity = (payload.get("severity") or "warn").strip()

        if payload.get("suppress_escalation"):
            return
        if self._skip_host_pressure_noise(code):
            return

        now = time.time()
        if (
            self._last_safe_mode_exit > 0
            and (now - self._last_safe_mode_exit) < self._reentry_cooldown_sec
            and code in self._HOST_PRESSURE_CODES
        ):
            return
        recent = self._recent[code]
        recent.append(now)
        cutoff = now - self._window_sec
        self._recent[code] = [t for t in recent if t > cutoff]

        if len(self._recent[code]) >= self._max_anomalies and severity == "high":
            logger.warning(
                "[healer] AnomalyEscalator: code=%s fired %d× in %ds — escalating",
                code, len(self._recent[code]), self._window_sec,
            )
            try:
                from core.resilience_controller import ResilienceController
                rc = ResilienceController()
                if rc.is_enabled() and not rc.is_safe_mode():
                    rc.enter_safe_mode(
                        f"anomaly escalation: {code} ×{len(self._recent[code])} "
                        f"in {self._window_sec}s",
                        level="safe",
                    )
                    bus.emit("healer.action", {
                        "healer": "AnomalyEscalator",
                        "action": "enter_safe_mode",
                        "reason": f"anomaly {code} threshold exceeded",
                        "details": {"code": code, "count": len(self._recent[code])},
                    })
            except Exception as e:
                logger.warning("[healer] AnomalyEscalator escalation failed: %s", e)

    def snapshot(self) -> Dict[str, Any]:
        now = time.time()
        return {
            "recent": {
                k: len([t for t in v if t > now - self._window_sec])
                for k, v in self._recent.items()
            },
            "window_sec": self._window_sec,
            "max_anomalies": self._max_anomalies,
        }


# ─── MaintenanceBridge ───────────────────────────────────────────────────

class MaintenanceBridge:
    """Слушает maintenance.tick → запускает maintenance цикл self_healing + rollback check."""

    async def __call__(self, payload: Dict[str, Any]) -> None:
        try:
            from core.self_healing import SelfHealingEngine
            engine = SelfHealingEngine.get_instance()
            if engine and hasattr(engine, "maintenance_tick"):
                await engine.maintenance_tick()
        except Exception as e:
            logger.debug("[healer] MaintenanceBridge error: %s", e)

        # Фаза 6: AutoRollback проверка pending undo-записей
        try:
            from core.auto_rollback import get_rollback_engine
            await get_rollback_engine().check_pending()
        except Exception as e:
            logger.debug("[healer] MaintenanceBridge rollback check error: %s", e)

        # Фаза 7: Meta-Cognitive Engine tick
        try:
            from core.meta_cognitive_engine import get_mce
            await get_mce().tick()
        except Exception as e:
            logger.debug("[healer] MaintenanceBridge MCE tick error: %s", e)

        # Фаза 9: Code Evolution — auto-optimizer tick
        try:
            from core.code_evolution import get_auto_optimizer
            get_auto_optimizer().tick()
        except Exception as e:
            logger.debug("[healer] MaintenanceBridge code-evol tick error: %s", e)


# ─── AutoLatencyHealer ──────────────────────────────────────────────────

class AutoLatencyHealer:
    """
    Слушает openrouter.done → отслеживает p95 латентности.
    При p95 > threshold → auto-setenv MODEL_SWITCH_THRESHOLD (увеличивает).

    Безопасные лимиты:
      - Максимум 3 авто-действия за час (чтобы не раскручивать порог бесконечно).
      - Абсолютный потолок MODEL_SWITCH_THRESHOLD = 20000ms.
      - Если после двух действий p95 не опустился ниже threshold —
        эскалация прекращается (латентность не от переключения модели).
    """

    def __init__(self) -> None:
        self._latencies_ms: Deque[float] = deque(maxlen=200)
        self._p95_threshold_ms = float(os.getenv("HEALER_LATENCY_P95_THRESHOLD_MS", "10000"))
        self._cooldown_sec = float(os.getenv("HEALER_LATENCY_COOLDOWN_SEC", "300"))
        self._last_action = 0.0
        self._actions_taken = 0
        self._max_actions = int(os.getenv("HEALER_LATENCY_MAX_ACTIONS", "3"))
        self._max_actions_window_sec = float(os.getenv("HEALER_LATENCY_MAX_ACTIONS_WINDOW_SEC", "3600"))
        self._action_window: Deque[float] = deque(maxlen=self._max_actions)
        self._hard_cap_ms = float(os.getenv("HEALER_LATENCY_HARD_CAP_MS", "20000"))

    async def __call__(self, payload: Dict[str, Any]) -> None:
        latency_ms = payload.get("latency_ms", 0.0)
        if not isinstance(latency_ms, (int, float)) or latency_ms <= 0:
            return
        self._latencies_ms.append(float(latency_ms))

        # Нужно минимум 20 выборок для p95
        if len(self._latencies_ms) < 20:
            return

        now = time.time()
        if now - self._last_action < self._cooldown_sec:
            return

        # Проверка: не превышен ли лимит действий в окне
        self._action_window.append(now)
        self._action_window = deque(
            [t for t in self._action_window if now - t < self._max_actions_window_sec],
            maxlen=self._max_actions,
        )
        if len(self._action_window) >= self._max_actions:
            return  # Лимит действий за час — прекращаем эскалацию

        sorted_lats = sorted(self._latencies_ms)
        p95 = sorted_lats[int(len(sorted_lats) * 0.95)]

        if p95 > self._p95_threshold_ms:
            self._last_action = now
            self._actions_taken += 1
            await self._apply_heal(p95)

    async def _apply_heal(self, p95: float) -> None:
        try:
            from core.env_flags import env_truthy

            if not env_truthy("HEALERS_ENV_MUTATION_ENABLED", default=True):
                logger.debug("[healer] AutoLatencyHealer: HEALERS_ENV_MUTATION_ENABLED=false — skip setenv")
                return
            current_threshold = float(os.getenv("MODEL_SWITCH_THRESHOLD", "8000"))
            new_threshold = min(current_threshold * 1.5, self._hard_cap_ms)

            # Регрессия: если порог уже высокий, а p95 не падает — не эскалируем
            if current_threshold >= self._hard_cap_ms * 0.75:
                logger.info(
                    "[healer] AutoLatencyHealer: threshold already at %d (cap %.0f) — skipping (latency not model-switch related)",
                    int(current_threshold), self._hard_cap_ms,
                )
                bus.emit("healer.failed", {
                    "healer": "AutoLatencyHealer",
                    "action": "skip",
                    "reason": f"threshold already at {int(current_threshold)}ms, p95={p95:.0f}ms — not model-switch related",
                })
                return

            from core.heal_executor import apply_steps
            result = await apply_steps(
                [f"env MODEL_SWITCH_THRESHOLD={int(new_threshold)}"],
                reason=f"auto_heal_latency: p95={p95:.0f}ms > {self._p95_threshold_ms:.0f}ms",
            )
            logger.info(
                "[healer] AutoLatencyHealer: p95=%.0fms → set MODEL_SWITCH_THRESHOLD=%d ok=%s (actions=%d/%d)",
                p95, int(new_threshold), result.get("ok"),
                len(self._action_window), self._max_actions,
            )
            # Undo-лог для возможного отката
            try:
                from core.auto_rollback import get_undo_log
                get_undo_log().add(
                    healer="AutoLatencyHealer",
                    action="set_env",
                    params={
                        "key": "MODEL_SWITCH_THRESHOLD",
                        "old_value": str(int(current_threshold)),
                        "new_value": str(int(new_threshold)),
                        "old_p95": p95,
                        "threshold_ms": self._p95_threshold_ms,
                    },
                    verify_window_sec=300.0,  # 5 минут на проверку
                )
            except Exception as e:
                logger.debug('%s optional failed: %s', 'event_healers', e, exc_info=True)
            bus.emit("anomaly.detected", {
                "code": "latency_p95_high",
                "severity": "warn",
                "details": {"p95_ms": p95, "new_threshold": int(new_threshold)},
            })
        except Exception as e:
            logger.warning("[healer] AutoLatencyHealer error: %s", e)

    def snapshot(self) -> Dict[str, Any]:
        sorted_lats = sorted(self._latencies_ms) if self._latencies_ms else []
        p95 = sorted_lats[int(len(sorted_lats) * 0.95)] if len(sorted_lats) >= 20 else 0.0
        return {
            "samples": len(self._latencies_ms),
            "p95_ms": round(p95, 1),
            "p95_threshold_ms": self._p95_threshold_ms,
            "actions_taken": self._actions_taken,
            "cooldown_sec": self._cooldown_sec,
            "max_actions": self._max_actions,
            "actions_in_window": len(self._action_window),
            "hard_cap_ms": self._hard_cap_ms,
        }


# ─── AutoFailRatioHealer ────────────────────────────────────────────────

class AutoFailRatioHealer:
    """
    Слушает openrouter.done → отслеживает ratio fail/ok.
    При fail_ratio > threshold → emit anomaly + рекомендация в healers.
    """

    def __init__(self) -> None:
        self._window: Deque[bool] = deque(maxlen=100)  # True=ok, False=fail
        self._fail_ratio_threshold = float(os.getenv("HEALER_FAIL_RATIO_THRESHOLD", "0.3"))
        self._cooldown_sec = float(os.getenv("HEALER_FAIL_RATIO_COOLDOWN_SEC", "300"))
        self._last_action = 0.0
        self._actions_taken = 0

    async def __call__(self, payload: Dict[str, Any]) -> None:
        ok = bool(payload.get("ok", False))
        self._window.append(ok)

        if len(self._window) < 20:
            return

        now = time.time()
        if now - self._last_action < self._cooldown_sec:
            return

        fail_count = sum(1 for v in self._window if not v)
        ratio = fail_count / len(self._window)

        if ratio > self._fail_ratio_threshold:
            self._last_action = now
            self._actions_taken += 1
            await self._apply_heal(ratio, fail_count)

    async def _apply_heal(self, ratio: float, fail_count: int) -> None:
        try:
            logger.info(
                "[healer] AutoFailRatioHealer: fail_ratio=%.2f (%d/%d) — emitting anomaly",
                ratio, fail_count, len(self._window),
            )
            bus.emit("anomaly.detected", {
                "code": "openrouter_fail_ratio_high",
                "severity": "high",
                "details": {
                    "fail_ratio": round(ratio, 3),
                    "fail_count": fail_count,
                    "window_size": len(self._window),
                },
            })
        except Exception as e:
            logger.warning("[healer] AutoFailRatioHealer error: %s", e)

    def snapshot(self) -> Dict[str, Any]:
        fail_count = sum(1 for v in self._window if not v) if self._window else 0
        return {
            "samples": len(self._window),
            "fail_count": fail_count,
            "fail_ratio": round(fail_count / len(self._window), 3) if self._window else 0.0,
            "threshold": self._fail_ratio_threshold,
            "actions_taken": self._actions_taken,
        }


# ─── AutoHostPressureHealer ────────────────────────────────────────────

class AutoHostPressureHealer:
    """
    Слушает maintenance.tick → проверяет host_resources.
    При resource_pressure_is_critical → emit anomaly + heal steps.
    """

    def __init__(self) -> None:
        self._cooldown_sec = float(os.getenv("HEALER_HOST_COOLDOWN_SEC", "600"))
        self._last_action = 0.0
        self._actions_taken = 0

    async def __call__(self, payload: Dict[str, Any]) -> None:
        now = time.time()
        if now - self._last_action < self._cooldown_sec:
            return

        try:
            from core.host_resources import (
                get_host_resource_snapshot,
                resource_pressure_degrades_system,
                resource_pressure_escalation_enabled,
            )

            if not resource_pressure_degrades_system() and not resource_pressure_escalation_enabled():
                return

            snap = get_host_resource_snapshot(force=True)
            pressure = snap.get("pressure", {})
            level = pressure.get("level", "ok")
            reasons = pressure.get("reasons", [])

            if level == "ok":
                return

            self._last_action = now
            self._actions_taken += 1

            logger.info(
                "[healer] AutoHostPressureHealer: pressure=%s reasons=%s",
                level, reasons,
            )

            bus.emit("anomaly.detected", {
                "code": "host_pressure",
                "severity": "high" if level == "critical" else "warn",
                "details": {
                    "level": level,
                    "reasons": reasons,
                    "available": snap.get("available"),
                },
            })

            if level == "critical":
                try:
                    from core.env_flags import env_truthy

                    if not env_truthy("HEALERS_ENV_MUTATION_ENABLED", default=True):
                        logger.debug(
                            "[healer] AutoHostPressureHealer: HEALERS_ENV_MUTATION_ENABLED=false — skip setenv"
                        )
                        return
                    from core.heal_executor import apply_steps
                    steps = [
                        "env HEAVY_MODULES_UNDER_PRESSURE=rag,books_rag,vision_describe,vision_ocr,image_generator"
                    ]
                    await apply_steps(steps, reason=f"auto_heal_host_pressure:{level}")
                except Exception as e:
                    logger.debug('%s optional failed: %s', 'event_healers', e, exc_info=True)
        except Exception as e:
            logger.debug("[healer] AutoHostPressureHealer error: %s", e)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "actions_taken": self._actions_taken,
            "cooldown_sec": self._cooldown_sec,
        }


# ─── Регистрация ─────────────────────────────────────────────────────────

_module_failure_healer = ModuleFailureHealer()
_bug_context_gatherer = BugContextGatherer()
_anomaly_escalator = AnomalyEscalator()
_maintenance_bridge = MaintenanceBridge()
_auto_latency_healer = AutoLatencyHealer()
_auto_fail_ratio_healer = AutoFailRatioHealer()
_auto_host_pressure_healer = AutoHostPressureHealer()

_INSTALLED = False


def install_healers() -> None:
    """Идемпотентная регистрация всех healers."""
    global _INSTALLED
    if _INSTALLED:
        return
    try:
        from core.env_flags import env_truthy

        if not env_truthy("HEALERS_ENABLED", default=True):
            logger.info("[healers] HEALERS_ENABLED=false — skip install")
            return
    except Exception as e:
        logger.debug("healers enabled check: %s", e)

    bus.subscribe_async("module.executed", _module_failure_healer)
    bus.subscribe_async("module.failed", _module_failure_healer)
    bus.subscribe_async("bug_report.collected", _bug_context_gatherer)
    bus.subscribe_async("anomaly.detected", _anomaly_escalator)
    bus.subscribe_async("resilience.safe_mode_cleared", _anomaly_escalator._on_safe_mode_cleared)
    bus.subscribe_async("maintenance.tick", _maintenance_bridge)
    bus.subscribe_async("openrouter.done", _auto_latency_healer)
    bus.subscribe_async("openrouter.done", _auto_fail_ratio_healer)
    bus.subscribe_async("maintenance.tick", _auto_host_pressure_healer)
    # Auto-re-enable: проверка disabled модулей на каждом tick
    async def _re_enable_on_tick(_payload: Dict[str, Any]) -> None:
        _module_failure_healer._check_re_enable()
    bus.subscribe_async("maintenance.tick", _re_enable_on_tick)

    _INSTALLED = True
    logger.info(
        "[healers] installed: ModuleFailureHealer, BugContextGatherer, "
        "AnomalyEscalator, MaintenanceBridge, AutoLatencyHealer, "
        "AutoFailRatioHealer, AutoHostPressureHealer"
    )


def get_module_failure_healer() -> ModuleFailureHealer:
    return _module_failure_healer


def get_anomaly_escalator() -> AnomalyEscalator:
    return _anomaly_escalator


def healers_snapshot() -> Dict[str, Any]:
    return {
        "module_failure_healer": _module_failure_healer.snapshot(),
        "anomaly_escalator": _anomaly_escalator.snapshot(),
        "auto_latency_healer": _auto_latency_healer.snapshot(),
        "auto_fail_ratio_healer": _auto_fail_ratio_healer.snapshot(),
        "auto_host_pressure_healer": _auto_host_pressure_healer.snapshot(),
        "installed": _INSTALLED,
    }


# Сброс при включении модуля
bus.subscribe("module.enabled", lambda _p: _reset_on_module_enable())


def _reset_on_module_enable() -> None:
    pass


__all__ = [
    "install_healers",
    "ModuleFailureHealer",
    "BugContextGatherer",
    "AnomalyEscalator",
    "MaintenanceBridge",
    "AutoLatencyHealer",
    "AutoFailRatioHealer",
    "AutoHostPressureHealer",
    "get_module_failure_healer",
    "get_anomaly_escalator",
    "healers_snapshot",
]
