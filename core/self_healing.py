"""
Self-Healing Engine 2.0 for the Universal Social Assistant
v3.0.0: enhanced detection for tool-failures, reasoning-timeouts,
collapse-overflows, KV-drift. Auto-resets context, reduces reasoning depth,
restarts KV-session when anomalies detected.
"""

import asyncio
import logging
import os
import time
from typing import Dict, List, Optional, Any
from datetime import datetime

from core.library_loader import LibraryLoader
from core.module_loader import ModuleLoader
from core.error_analysis import record_error_event

logger = logging.getLogger(__name__)

SELF_HEALING_VERSION = "4.0.0"

_error_counter: Dict[str, int] = {}
AUTO_RESET_THRESHOLD = 3

_OPTIMIZATION_WINDOW_SEC = 600
_response_times: List[float] = []
_error_timestamps: List[float] = []
_tool_call_counts: List[int] = []
_last_analysis_ts: float = 0.0
_last_maintenance_ts: float = 0.0

# ── Self-Healing 2.0 anomaly tracking ──
_anomalies: Dict[str, int] = {
    "tool_failures": 0,
    "reasoning_timeouts": 0,
    "collapse_overflows": 0,
    "kv_drift": 0,
}
_anomaly_timestamps: List[Dict[str, Any]] = []
_anomaly_thresholds = {
    "tool_failures": 3,
    "reasoning_timeouts": 2,
    "collapse_overflows": 2,
    "kv_drift": 2,
}

_engine_singleton: Optional["SelfHealingEngine"] = None


class SelfHealingEngine:
    """
    Engine for automatic self-healing of modules and libraries.
    Full version, corrected, stable.
    """

    def __init__(self):
        self.library_loader = LibraryLoader()
        self.module_loader = ModuleLoader()
        self.self_programming = None
        self.is_running = False
        self.plugin_registry = None

    @classmethod
    def get_instance(cls) -> "SelfHealingEngine":
        """Singleton для event healers и maintenance bridge."""
        global _engine_singleton
        if _engine_singleton is None:
            _engine_singleton = cls()
        return _engine_singleton

    async def maintenance_tick(self) -> None:
        """Публичный maintenance tick для healers."""
        await self._maintenance_tick()

    async def start_monitoring(self, plugin_registry=None, interval: int = 300):
        self.is_running = True
        self.plugin_registry = plugin_registry
        logger.info("Starting self-healing monitoring...")

        while self.is_running:
            try:
                for lib_name in list(self.library_loader.registry.keys()):
                    status = self.library_loader.get_library_status(lib_name)
                    if status == "broken":
                        logger.warning(f"[Self-Healing] Broken library detected: {lib_name}")
                        await self.attempt_repair_library(lib_name)

                if self.plugin_registry:
                    module_states = self.plugin_registry.get_module_states()
                    for module_state in module_states:
                        if module_state.status == "failed":
                            logger.warning(f"[Self-Healing] Failed module detected: {module_state.name}")
                            await self.attempt_repair_module(module_state.name)

                # Self-Healing 2.0: check anomalies and auto-heal
                self._check_and_heal_anomalies()

                # Maintenance tick: integrity checks + cleanup
                await self._maintenance_tick()

                await asyncio.sleep(interval)
            except Exception as e:
                logger.error(f"Error in self-healing monitoring: {e}")
                await asyncio.sleep(60)

    def _check_and_heal_anomalies(self) -> None:
        """Check thresholds and perform auto-healing actions."""
        global _anomalies

        # Tool failures → reset context (через EventBus)
        if _anomalies.get("tool_failures", 0) >= _anomaly_thresholds["tool_failures"]:
            logger.warning("[Self-Healing] tool_failures threshold exceeded — resetting context")
            _anomalies["tool_failures"] = 0
            try:
                from core.event_bus import EventBus
                bus = EventBus.get_instance()
                import asyncio
                asyncio.ensure_future(bus.emit(
                    "context.reset",
                    {"reason": "tool_failures_healed", "source": "self_healing"},
                ))
            except Exception as e:
                logger.debug("[Self-Healing] context reset event error: %s", e)

        # Reasoning timeouts → reduce reasoning depth через os.environ
        if _anomalies.get("reasoning_timeouts", 0) >= _anomaly_thresholds["reasoning_timeouts"]:
            logger.warning("[Self-Healing] reasoning_timeouts threshold exceeded — reducing depth")
            _anomalies["reasoning_timeouts"] = 0
            current_depth = int(os.environ.get("REASONING_DEPTH", "5"))
            new_depth = max(1, current_depth - 1)
            os.environ["REASONING_DEPTH"] = str(new_depth)
            logger.info("[Self-Healing] REASONING_DEPTH reduced: %d → %d", current_depth, new_depth)

        # Collapse overflows → reset context
        if _anomalies.get("collapse_overflows", 0) >= _anomaly_thresholds["collapse_overflows"]:
            logger.warning("[Self-Healing] collapse_overflows threshold exceeded — resetting context")
            _anomalies["collapse_overflows"] = 0
            try:
                from core.event_bus import EventBus
                bus = EventBus.get_instance()
                import asyncio
                asyncio.ensure_future(bus.emit(
                    "context.reset",
                    {"reason": "collapse_overflow_healed", "source": "self_healing"},
                ))
            except Exception as e:
                logger.debug("[Self-Healing] context reset event error: %s", e)

        # KV drift → restart KV-session через очистку кеша
        if _anomalies.get("kv_drift", 0) >= _anomaly_thresholds["kv_drift"]:
            logger.warning("[Self-Healing] kv_drift threshold exceeded — restarting KV session")
            _anomalies["kv_drift"] = 0
            try:
                from core.event_bus import EventBus
                bus = EventBus.get_instance()
                import asyncio
                asyncio.ensure_future(bus.emit(
                    "kv.reset",
                    {"reason": "kv_drift_healed", "source": "self_healing"},
                ))
            except Exception as e:
                logger.debug("[Self-Healing] KV reset event error: %s", e)

    async def stop_monitoring(self):
        self.is_running = False
        logger.info("Stopping self-healing monitoring...")

    async def attempt_repair_module(self, module_name: str) -> bool:
        try:
            logger.info(f"[Self-Healing] Attempting to repair module: {module_name}")
            module_path = self.module_loader.modules_path / module_name
            if module_path.exists():
                result = await self.module_loader.load_module(module_path)
                if result:
                    logger.info(f"[Self-Healing] Module {module_name} repaired by reload")
                    return True
            logger.warning(f"[Self-Healing] Could not repair module {module_name}")
            record_error_event(
                "self_healing",
                "module repair exhausted",
                extra={"module": module_name},
            )
            return False
        except Exception as e:
            logger.error(f"[Self-Healing] Failed to repair module {module_name}: {e}")
            record_error_event("self_healing", "module repair exception", exc=e, extra={"module": module_name})
            return False

    async def attempt_repair_library(self, library_name: str) -> bool:
        try:
            logger.info(f"[Self-Healing] Attempting to repair library: {library_name}")
            fallback = self.library_loader.get_fallback_library(library_name)
            if fallback:
                logger.info(f"[Self-Healing] Using fallback library {fallback} for {library_name}")
                return True
            logger.warning(f"[Self-Healing] No fallback available for library {library_name}")
            return False
        except Exception as e:
            logger.error(f"[Self-Healing] Failed to repair library {library_name}: {e}")
            return False

    async def _maintenance_tick(self) -> None:
        """Периодическая самодиагностика с действием: KV/DB integrity, pending, temp files."""
        await self._check_kv_integrity()
        await self._check_db_integrity()
        await self._cleanup_stale_pending()
        await self._cleanup_temp_files()

    async def _check_kv_integrity(self) -> None:
        try:
            from core.agent_kv.store import integrity_check, repair_kv_store
            check = integrity_check()
            if not check.get("ok") and check.get("enabled"):
                logger.warning("[self_healing] KV integrity failed: %s", check.get("error"))
                try:
                    from core.monitoring import MONITOR
                    MONITOR.inc("self_healing_kv_integrity_false_total")
                except Exception as e:
                    logger.debug('%s optional failed: %s', 'self_healing', e, exc_info=True)
                repair = repair_kv_store()
                if repair.get("repaired"):
                    logger.info(
                        "[self_healing] KV repaired: backup=%s",
                        repair.get("backup"),
                    )
                    try:
                        from core.monitoring import MONITOR
                        MONITOR.inc("self_healing_kv_repaired_total")
                    except Exception as e:
                        logger.debug('%s optional failed: %s', 'self_healing', e, exc_info=True)
        except Exception as e:
            logger.warning("[self_healing] KV integrity check error: %s", e)

    async def _check_db_integrity(self) -> None:
        import os as _os
        from core.database import engine
        try:
            with engine.connect() as conn:
                row = conn.exec_driver_sql("PRAGMA quick_check").scalar_one_or_none()
            if row and str(row).lower() != "ok":
                logger.warning("[self_healing] DB integrity failed: %s", row)
                try:
                    from core.monitoring import MONITOR
                    MONITOR.inc("self_healing_db_integrity_false_total")
                except Exception as e:
                    logger.debug('%s optional failed: %s', 'self_healing', e, exc_info=True)
        except Exception as e:
            logger.warning("[self_healing] DB integrity check error: %s", e)

    async def _cleanup_stale_pending(self) -> None:
        try:
            from core.pending_flow import clear_all_pending, has_any_pending
            if has_any_pending("", ""):
                logger.debug("[self_healing] pending sources registered, checking staleness")
        except Exception as e:
            logger.debug("[self_healing] pending check skipped: %s", e)

    async def _cleanup_temp_files(self) -> None:
        import os as _os
        import shutil
        from pathlib import Path
        try:
            now = time.time()
            cleaned = 0
            for temp_dir in [Path("data", "temp"), Path("data", "tmp")]:
                if temp_dir.is_dir():
                    for entry in temp_dir.iterdir():
                        if entry.is_file():
                            try:
                                age_h = (now - entry.stat().st_mtime) / 3600
                                if age_h > 1:
                                    entry.unlink()
                                    cleaned += 1
                            except OSError:
                                pass
            root = Path(_os.getcwd())
            for pycache in root.rglob("__pycache__"):
                try:
                    age_h = (now - pycache.stat().st_mtime) / 3600
                    if age_h > 24:
                        shutil.rmtree(pycache)
                        cleaned += 1
                except OSError:
                    pass
            if cleaned:
                logger.info("[self_healing] cleaned %s stale temp files/caches", cleaned)
        except Exception as e:
            logger.debug("[self_healing] temp cleanup error: %s", e)


# ── Self-Healing 2.0 anomaly recording ──

def record_tool_failure(tool_name: str = "", message: str = "") -> None:
    global _anomalies
    _anomalies["tool_failures"] = _anomalies.get("tool_failures", 0) + 1
    _anomaly_timestamps.append({
        "type": "tool_failure",
        "tool": tool_name,
        "message": message[:300],
        "ts": time.time(),
    })
    if len(_anomaly_timestamps) > 100:
        _anomaly_timestamps[:] = _anomaly_timestamps[-50:]


def record_reasoning_timeout() -> None:
    global _anomalies
    _anomalies["reasoning_timeouts"] = _anomalies.get("reasoning_timeouts", 0) + 1
    _anomaly_timestamps.append({
        "type": "reasoning_timeout",
        "ts": time.time(),
    })
    if len(_anomaly_timestamps) > 100:
        _anomaly_timestamps[:] = _anomaly_timestamps[-50:]


def record_collapse_overflow() -> None:
    global _anomalies
    _anomalies["collapse_overflows"] = _anomalies.get("collapse_overflows", 0) + 1
    _anomaly_timestamps.append({
        "type": "collapse_overflow",
        "ts": time.time(),
    })
    if len(_anomaly_timestamps) > 100:
        _anomaly_timestamps[:] = _anomaly_timestamps[-50:]


def record_kv_drift() -> None:
    global _anomalies
    _anomalies["kv_drift"] = _anomalies.get("kv_drift", 0) + 1
    _anomaly_timestamps.append({
        "type": "kv_drift",
        "ts": time.time(),
    })
    if len(_anomaly_timestamps) > 100:
        _anomaly_timestamps[:] = _anomaly_timestamps[-50:]


def get_anomalies() -> Dict[str, Any]:
    return {
        "counts": dict(_anomalies),
        "recent": _anomaly_timestamps[-10:] if _anomaly_timestamps else [],
        "thresholds": dict(_anomaly_thresholds),
    }


def reset_anomalies() -> None:
    global _anomalies
    _anomalies = {
        "tool_failures": 0,
        "reasoning_timeouts": 0,
        "collapse_overflows": 0,
        "kv_drift": 0,
    }
    _anomaly_timestamps.clear()


# ── Legacy functions (preserved for backward compatibility) ──

def log_tool_error(tool_name: str, step_index: int, message: str) -> None:
    logger.warning(
        "[SELF_HEALING] tool_error tool=%s step=%d error=%s",
        tool_name, step_index, message,
    )
    record_error_event(
        "self_healing_tool_error",
        f"tool={tool_name} step={step_index}",
        extra={"tool": tool_name, "step_index": step_index, "error": message},
    )
    _error_counter["tool_error"] = _error_counter.get("tool_error", 0) + 1
    # Self-Healing 2.0: also record as anomaly
    record_tool_failure(tool_name, message)


def get_error_count() -> int:
    return sum(_error_counter.values())


def should_auto_reset() -> bool:
    total = get_error_count()
    if total >= AUTO_RESET_THRESHOLD:
        _error_counter.clear()
        logger.error(
            "[SELF_HEALING] auto-reset triggered after %d consecutive errors",
            total,
        )
        return True
    return False


def reset_error_counters() -> None:
    _error_counter.clear()


# ── Self-Optimization Loop (Autonomy 3.0) ──

def record_response_time(seconds: float) -> None:
    _response_times.append(seconds)
    if len(_response_times) > 200:
        _response_times[:] = _response_times[-100:]


def record_error_ts() -> None:
    _error_timestamps.append(time.time())
    if len(_error_timestamps) > 100:
        _error_timestamps[:] = _error_timestamps[-50:]


def record_tool_call_count(count: int) -> None:
    _tool_call_counts.append(count)
    if len(_tool_call_counts) > 100:
        _tool_call_counts[:] = _tool_call_counts[-50:]


def analyze_and_optimize() -> Dict[str, Any]:
    """Analyze recent metrics and return optimization actions."""
    now = time.time()
    recent_rts = [v for v in _response_times[-20:] if now - _last_analysis_ts <= _OPTIMIZATION_WINDOW_SEC]
    recent_errs = [v for v in _error_timestamps if now - v <= _OPTIMIZATION_WINDOW_SEC]

    suggestions: List[str] = []

    avg_rt = sum(recent_rts) / len(recent_rts) if recent_rts else 0.0
    if avg_rt > 8.0:
        suggestions.append("уменьшить глубину reasoning")
    if len(recent_errs) > 5:
        suggestions.append("изменить порядок инструментов")

    recent_tools = _tool_call_counts[-10:] if _tool_call_counts else []
    if recent_tools and sum(recent_tools) / len(recent_tools) > 5:
        suggestions.append("отключить неэффективный tool-chain")

    if avg_rt > 5.0 or len(recent_errs) > 3:
        suggestions.append("предложить улучшение пользователю")

    # Self-Healing 2.0: include anomaly state
    anomaly_summary = get_anomalies()

    return {
        "avg_response_time": round(avg_rt, 3),
        "recent_errors": len(recent_errs),
        "suggestions": suggestions,
        "anomalies": anomaly_summary,
    }
