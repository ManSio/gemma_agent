from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.development_passport import get_development_passport, rollback_passport_to_latest_backup
from core.diagnostics import build_diagnostic_snapshot
from core.host_resources import (
    get_host_resource_snapshot,
    resource_pressure_degrades_system,
    resource_pressure_escalation_enabled,
)
from core.error_analysis import aggregate_error_stats, record_error_event
from core.monitoring import MONITOR

logger = logging.getLogger(__name__)

# manifest.name у chat — «chat-orchestrator», в SAFE_MODE часто пишут «chat_orchestrator»
_MODULE_ALLOWLIST_ALIAS_GROUPS = (frozenset({"chat-orchestrator", "chat_orchestrator"}),)


def expand_module_allowlist_ids(names: set) -> set:
    """Расширяет allowlist синонимами id модулей (пересечение с loaded_modules)."""
    out = set(names)
    for group in _MODULE_ALLOWLIST_ALIAS_GROUPS:
        if names & group:
            out |= group
    return out


def _runtime_dir() -> Path:
    p = Path(os.getenv("RESILIENCE_RUNTIME_DIR", "data/runtime"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _safe_mode_path() -> Path:
    return _runtime_dir() / "safe_mode_state.json"


def _restart_path() -> Path:
    return _runtime_dir() / "restart_requested.json"


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class ResilienceController:
    """
    Обнаружение деградации (KPI паспорта, ошибки, модули, stop-rules),
    safe-mode, откат паспорта, запрос перезапуска контейнера, выход в норму после стабилизации.
    """

    def __init__(self) -> None:
        self._enabled = os.getenv("RESILIENCE_AUTONOMY_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
        self._safe_err = max(10, int(os.getenv("RESILIENCE_SAFE_ERROR_TOTAL", "60")))
        self._crit_err = max(self._safe_err + 1, int(os.getenv("RESILIENCE_CRITICAL_ERROR_TOTAL", "120")))
        self._crit_failed_mod = max(1, int(os.getenv("RESILIENCE_CRITICAL_FAILED_MODULES", "3")))
        raw = os.getenv(
            "SAFE_MODE_MODULE_ALLOWLIST",
            "chat-orchestrator,math,echo,external_apis,memory",
        )
        self._allowlist = {x.strip() for x in raw.split(",") if x.strip()}
        self._recovery_cycles = max(1, int(os.getenv("RESILIENCE_RECOVERY_OK_CYCLES", "2")))
        self._auto_clear_restart_flag = os.getenv("RESILIENCE_AUTO_CLEAR_RESTART_FLAG", "true").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    def is_enabled(self) -> bool:
        return self._enabled

    def is_safe_mode(self) -> bool:
        st = _read_json(_safe_mode_path())
        return bool(st.get("active"))

    def safe_mode_allowlist(self) -> set:
        return expand_module_allowlist_ids(set(self._allowlist))

    def snapshot(self) -> Dict[str, Any]:
        return {
            "enabled": self._enabled,
            "safe_mode": _read_json(_safe_mode_path()),
            "restart_requested": _read_json(_restart_path()),
            "allowlist": sorted(self._allowlist),
        }

    def enter_safe_mode(self, reason: str, *, level: str = "safe") -> None:
        data = {
            "active": True,
            "since": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "level": level,
            "recovery_ok_streak": 0,
        }
        _write_json(_safe_mode_path(), data)
        MONITOR.inc("resilience_safe_mode_enter_total")
        sev = "error" if level == "critical" else "warning"
        record_error_event("resilience", f"safe_mode entered: {reason}", extra={"level": level}, severity=sev)
        logger.warning("Resilience: SAFE MODE — %s (%s)", reason, level)

    def exit_safe_mode(self, reason: str) -> None:
        _write_json(_safe_mode_path(), {"active": False, "cleared_at": datetime.now(timezone.utc).isoformat(), "reason": reason})
        MONITOR.inc("resilience_safe_mode_exit_total")
        record_error_event("resilience", f"safe_mode cleared: {reason}", severity="info")
        logger.info("Resilience: safe mode cleared — %s", reason)
        try:
            from core.event_bus import bus
            bus.emit("resilience.safe_mode_cleared", {"reason": reason})
        except Exception as e:
            logger.debug('%s optional failed: %s', 'resilience_controller', e, exc_info=True)
    def request_container_restart(self, reason: str) -> None:
        p = _restart_path()
        prior = _read_json(p)
        already = bool(prior.get("requested"))
        payload = {
            "requested": True,
            "reason": reason,
            "ts": datetime.now(timezone.utc).isoformat(),
            "hint": "Внешний оркестратор: прочитайте файл и выполните docker compose restart / kubectl rollout. "
            "Путь задаётся RESILIENCE_RUNTIME_DIR/restart_requested.json",
        }
        _write_json(p, payload)
        # Не раздуваем runtime_errors: severity=error участвует в порогах critical и создаёт петлю
        # (каждый critical-tick → новая строка → error_total растёт → снова critical).
        # Дубликаты при уже выставленном флаге не журналируем; счётчик метрики — только при первом запросе.
        if not already:
            MONITOR.inc("resilience_restart_flag_total")
            record_error_event("resilience", f"restart requested: {reason}", severity="info")
            logger.error("Resilience: RESTART REQUESTED — %s", reason)
        else:
            logger.info("Resilience: restart flag already set, updated reason only — %s", reason)

    def acknowledge_restart_if_pending(self) -> Optional[Dict[str, Any]]:
        p = _restart_path()
        data = _read_json(p)
        if not data.get("requested"):
            return None
        ack = {
            "acknowledged_at": datetime.now(timezone.utc).isoformat(),
            "prior": data,
        }
        _write_json(_runtime_dir() / "restart_acknowledged.json", ack)
        if p.is_file():
            try:
                p.unlink()
            except OSError:
                pass
        record_error_event("resilience", "restart flag acknowledged after boot", severity="info")
        return ack

    def clear_restart_request_if_pending(self, reason: str) -> bool:
        """Снимает залипший restart_requested без рестарта процесса (после восстановления метрик)."""
        p = _restart_path()
        data = _read_json(p)
        if not data.get("requested"):
            return False
        try:
            p.unlink()
        except OSError:
            return False
        record_error_event("resilience", f"restart flag cleared: {reason}", severity="info")
        logger.info("Resilience: restart flag cleared — %s", reason)
        MONITOR.inc("resilience_restart_flag_cleared_total")
        return True

    def evaluate(self, orchestrator: Any) -> Dict[str, Any]:
        snap = build_diagnostic_snapshot(orchestrator)
        passport_block = snap.get("development_passport") or {}
        kpi_eval = passport_block.get("kpi_eval") or {}
        kpi_ok = all(bool(v) for v in kpi_eval.values()) if kpi_eval else True
        err = aggregate_error_stats(
            limit=int(os.getenv("RESILIENCE_ERROR_SAMPLE", "500")),
            for_resilience=True,
        )
        total_err = int(err.get("total") or 0)
        info = orchestrator.get_system_info()
        overall = str(info.get("overall_status") or "")
        modules = info.get("modules") or []
        failed_cnt = sum(1 for m in modules if isinstance(m, dict) and m.get("status") == "failed")
        mon = snap.get("monitoring") or {}
        counters = mon.get("counters") or {} if isinstance(mon, dict) else {}

        passport = get_development_passport()
        kpi_targets = passport.get("kpi_targets") if isinstance(passport, dict) else {}
        if not isinstance(kpi_targets, dict):
            kpi_targets = {}
        stop_violations = self._evaluate_stop_rules(passport, counters, kpi_targets)

        host = get_host_resource_snapshot()
        pressure = host.get("pressure") or {}
        pr_level = str(pressure.get("level") or "ok")
        pr_reasons: List[str] = list(pressure.get("reasons") or [])

        degraded = (
            not kpi_ok
            or total_err >= self._safe_err
            or failed_cnt >= 1
            or overall == "degraded"
            or bool(stop_violations)
        )
        critical = (
            (not kpi_ok and total_err >= self._crit_err)
            or total_err >= self._crit_err
            or failed_cnt >= self._crit_failed_mod
            or overall == "failed"
            or len(stop_violations) >= 2
        )
        if resource_pressure_degrades_system() and pr_level in ("warn", "critical"):
            degraded = True
        if (
            resource_pressure_escalation_enabled()
            and pr_level == "critical"
            and any(
                r.startswith("memory_critical") or r.startswith("disk_critical") for r in pr_reasons
            )
        ):
            critical = True

        return {
            "kpi_eval": kpi_eval,
            "kpi_ok": kpi_ok,
            "error_total": total_err,
            "failed_modules": failed_cnt,
            # overall_status — сводка оркестратора по модулям; деградация ниже — по журналу runtime_errors
            "overall_status": overall,
            "modules_overall": overall,
            "error_thresholds": {
                "degraded_at": self._safe_err,
                "critical_at": self._crit_err,
            },
            "stop_rule_violations": stop_violations,
            "degraded": degraded,
            "critical": critical,
            "host_resources": {
                "available": host.get("available"),
                "cpu_percent": host.get("cpu_percent"),
                "memory": host.get("memory"),
                "pressure": pressure,
                "adaptation_hints": host.get("adaptation_hints"),
            },
        }

    def _evaluate_stop_rules(
        self,
        passport: Dict[str, Any],
        counters: Dict[str, Any],
        kpi_targets: Dict[str, Any],
    ) -> List[str]:
        violated: List[str] = []
        rules = passport.get("stop_rules") or []
        if not isinstance(rules, list):
            return violated
        fb = int(counters.get("planner_fallback_total", 0))
        fb_max = int(kpi_targets.get("planner_fallback_total_max", 10**9))
        sec = int(counters.get("security_high_risk_total", 0))
        sec_max = int(kpi_targets.get("security_high_risk_total_max", 10**9))
        for rule in rules:
            if not isinstance(rule, str):
                continue
            rl = rule.lower()
            if any(k in rl for k in ("routing", "planner", "fallback", "contract")):
                if fb > max(fb_max * 2, fb_max + 15):
                    violated.append(rule)
            if "safety" in rl or "security" in rl or "risk" in rl:
                if sec > max(sec_max * 2, sec_max + 3):
                    violated.append(rule)
        return violated

    def tick(self, orchestrator: Any, *, maintenance_ran: bool) -> Dict[str, Any]:
        if not self._enabled:
            return {"ran": False, "reason": "disabled"}
        if not maintenance_ran:
            return {"ran": False, "reason": "not_maintenance_tick"}

        ev = self.evaluate(orchestrator)
        out: Dict[str, Any] = {"ran": True, "evaluate": ev}

        if ev["critical"]:
            if not self.is_safe_mode():
                self.enter_safe_mode("critical degradation (KPI/errors/modules/stop-rules)", level="critical")
            try:
                from core.recovery_autonomy import backup_before_critical_mutations

                backup_before_critical_mutations("pre_critical_resilience")
            except Exception as e:
                logger.debug('%s optional failed: %s', 'resilience_controller', e, exc_info=True)
            try:
                rb = rollback_passport_to_latest_backup()
                out["passport_rollback"] = rb
            except Exception as e:
                out["passport_rollback_error"] = str(e)
            self.request_container_restart(
                f"critical: errors={ev['error_total']} failed_mod={ev['failed_modules']} kpi_ok={ev['kpi_ok']}"
            )
            MONITOR.inc("resilience_critical_actions_total")
            return out

        if ev["degraded"]:
            if not self.is_safe_mode():
                self.enter_safe_mode(
                    f"degraded: kpi_ok={ev['kpi_ok']} errors={ev['error_total']} violations={ev['stop_rule_violations']}",
                    level="safe",
                )
            else:
                st = _read_json(_safe_mode_path())
                st["recovery_ok_streak"] = 0
                st["active"] = True
                _write_json(_safe_mode_path(), st)
            return out

        # healthy path — выходим из safe-mode после стабильных циклов (быстрее после anomaly escalation)
        if self.is_safe_mode():
            st = _read_json(_safe_mode_path())
            reason = str(st.get("reason") or "")
            if "anomaly escalation" in reason.lower():
                self.exit_safe_mode("recovered: stable metrics after anomaly escalation")
                out["recovery_fast_path"] = True
            else:
                streak = int(st.get("recovery_ok_streak") or 0) + 1
                if streak >= self._recovery_cycles:
                    self.exit_safe_mode(f"stable health after {streak} maintenance cycles")
                else:
                    st["recovery_ok_streak"] = streak
                    st["active"] = True
                    _write_json(_safe_mode_path(), st)
                out["recovery_streak"] = streak
        if self._auto_clear_restart_flag and self.clear_restart_request_if_pending(
            "recovered: non-critical maintenance tick (errors/modules/KPI within bounds)"
        ):
            out["restart_flag_cleared"] = True
        return out

    def post_boot_recovery(self, orchestrator: Any) -> Dict[str, Any]:
        """После перезапуска процесса: подтвердить restart-flag, оценить здоровье, при необходимости снять safe-mode."""
        if not self._enabled:
            return {"ok": True, "reason": "disabled"}
        ack = self.acknowledge_restart_if_pending()
        ev = self.evaluate(orchestrator)
        result: Dict[str, Any] = {"ack": ack, "evaluate": ev}
        if not isinstance(ev, dict):
            logger.warning("post_boot_recovery: evaluate returned non-dict")
            return result
        if ev.get("error"):
            logger.warning("post_boot_recovery: evaluate failed, safe mode unchanged: %s", ev.get("error"))
            return result
        degraded = bool(ev.get("degraded"))
        critical = bool(ev.get("critical"))
        if self.is_safe_mode() and not degraded and not critical:
            self.exit_safe_mode("post_boot: metrics within bounds")
            result["cleared_safe_mode"] = True
        elif self.is_safe_mode() and degraded and not critical:
            st = _read_json(_safe_mode_path())
            st["recovery_ok_streak"] = 0
            st["active"] = True
            _write_json(_safe_mode_path(), st)
            result["safe_mode_kept"] = True
        return result
