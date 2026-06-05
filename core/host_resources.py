from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import psutil  # type: ignore
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore

_cpu_warmed = False
_cache: Dict[str, Any] = {}
_cache_ts = 0.0
_last_pressure_level = "ok"
_PROCESS_START_MONO = time.monotonic()


def _project_root() -> Path:
    r = os.getenv("PROJECT_ROOT", "").strip()
    return Path(r).resolve() if r else Path.cwd()


def _env_truthy(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _cpu_boot_grace_sec() -> float:
    return max(0.0, float(os.getenv("RESOURCE_CPU_BOOT_GRACE_SEC", "180")))


def _metrics_boot_delay_sec() -> float:
    """Не эскалировать pressure и не гонять блокирующий CPU-замер сразу после старта."""
    return max(0.0, float(os.getenv("RESOURCE_METRICS_BOOT_DELAY_SEC", "45")))


def _in_cpu_boot_grace() -> bool:
    return (time.monotonic() - _PROCESS_START_MONO) < _cpu_boot_grace_sec()


def _in_metrics_boot_delay() -> bool:
    return (time.monotonic() - _PROCESS_START_MONO) < _metrics_boot_delay_sec()


def _read_cpu_percent() -> float:
    """Блокирующий замер CPU (psutil interval=None после старта часто даёт 100%)."""
    if not psutil:
        raise RuntimeError("psutil_not_installed")
    interval = max(0.05, float(os.getenv("RESOURCE_CPU_SAMPLE_INTERVAL_SEC", "0.25")))
    return float(psutil.cpu_percent(interval=interval))


def _warm_cpu_sample() -> None:
    global _cpu_warmed
    if not psutil or _cpu_warmed:
        return
    try:
        _read_cpu_percent()
        _cpu_warmed = True
    except Exception as e:
        logger.debug("host_resources cpu warm: %s", e)


def _apply_cpu_boot_grace(pressure: Dict[str, Any]) -> Dict[str, Any]:
    """
    Сразу после старта процесса psutil часто даёт cpu_critical:100 без реальной перегрузки.
    Понижаем до warn, пока не истёк RESOURCE_CPU_BOOT_GRACE_SEC и критичны только CPU-причины.
    """
    if not _in_cpu_boot_grace():
        return pressure
    level = str(pressure.get("level") or "ok")
    reasons: List[str] = list(pressure.get("reasons") or [])
    if level != "critical" or not reasons:
        return pressure
    if not all(str(r).startswith("cpu_") for r in reasons):
        return pressure
    tagged = [f"{r}(boot_grace)" for r in reasons]
    logger.debug("host_resources: cpu-only critical downgraded during boot grace (%s)", tagged)
    return {"level": "warn", "reasons": tagged}


def _disk_usage_for_path(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists():
            return None
        u = shutil.disk_usage(str(path.resolve()))
        total = u.total or 1
        used = u.used
        free = u.free
        pct = round(100.0 * used / total, 2)
        return {
            "path": str(path),
            "total_gb": round(total / (1024**3), 2),
            "used_gb": round(used / (1024**3), 2),
            "free_gb": round(free / (1024**3), 2),
            "used_percent": pct,
        }
    except Exception as e:
        logger.debug("disk_usage %s: %s", path, e)
        return None


def _evaluate_pressure(
    cpu: Optional[float],
    mem_pct: Optional[float],
    disks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    warn_cpu = float(os.getenv("RESOURCE_WARN_CPU_PERCENT", "88"))
    warn_mem = float(os.getenv("RESOURCE_WARN_MEM_PERCENT", "88"))
    warn_disk = float(os.getenv("RESOURCE_WARN_DISK_PERCENT", "90"))
    crit_mem = float(os.getenv("RESOURCE_CRIT_MEM_PERCENT", "96"))
    crit_disk = float(os.getenv("RESOURCE_CRIT_DISK_PERCENT", "97"))
    crit_cpu = float(os.getenv("RESOURCE_CRIT_CPU_PERCENT", "98"))

    reasons: List[str] = []
    level = "ok"

    if cpu is not None:
        if cpu >= crit_cpu:
            reasons.append(f"cpu_critical:{cpu:.1f}")
            level = "critical"
        elif cpu >= warn_cpu:
            reasons.append(f"cpu_high:{cpu:.1f}")
            if level == "ok":
                level = "warn"

    if mem_pct is not None:
        if mem_pct >= crit_mem:
            reasons.append(f"memory_critical:{mem_pct:.1f}")
            level = "critical"
        elif mem_pct >= warn_mem:
            reasons.append(f"memory_high:{mem_pct:.1f}")
            if level != "critical":
                level = "warn"

    for d in disks:
        p = d.get("used_percent")
        if p is None:
            continue
        if p >= crit_disk:
            reasons.append(f"disk_critical:{d.get('path')}:{p:.1f}")
            level = "critical"
        elif p >= warn_disk:
            reasons.append(f"disk_high:{d.get('path')}:{p:.1f}")
            if level != "critical":
                level = "warn"

    return _apply_cpu_boot_grace({"level": level, "reasons": reasons})


def _snapshot_during_metrics_boot_delay() -> Dict[str, Any]:
    """RAM/диск без блокирующего cpu_percent — pressure всегда ok до конца задержки."""
    mem_pct: Optional[float] = None
    mem_used = 0
    mem_total = 0
    disks: List[Dict[str, Any]] = []

    def _root_path() -> Path:
        if os.name == "nt":
            return Path(os.environ.get("SystemDrive", "C:") + "\\")
        return Path("/")

    if psutil:
        try:
            vm = psutil.virtual_memory()
            mem_pct = float(vm.percent)
            mem_used = vm.used
            mem_total = vm.total
        except Exception as e:
            logger.debug("host_resources mem during boot delay: %s", e)
        for label, p in (
            ("root", _root_path()),
            ("project", _project_root()),
            ("data", _project_root() / "data"),
        ):
            du = _disk_usage_for_path(p)
            if du:
                du["label"] = label
                disks.append(du)

    try:
        load1, load5, load15 = os.getloadavg()
        load_avg = {"1m": round(load1, 2), "5m": round(load5, 2), "15m": round(load15, 2)}
    except (AttributeError, OSError):
        load_avg = {}

    delay_sec = _metrics_boot_delay_sec()
    elapsed = time.monotonic() - _PROCESS_START_MONO
    return {
        "available": bool(psutil),
        "cpu_percent": None,
        "memory": {
            "percent": round(mem_pct, 2) if mem_pct is not None else None,
            "used_mb": round(mem_used / (1024**2), 1) if mem_total else None,
            "total_mb": round(mem_total / (1024**2), 1) if mem_total else None,
        },
        "disk": disks,
        "load_avg": load_avg,
        "pressure": {"level": "ok", "reasons": []},
        "ts": time.time(),
        "adaptation_hints": [],
        "cpu_boot_grace_active": _in_cpu_boot_grace(),
        "metrics_boot_delay_active": True,
        "metrics_boot_delay_sec": delay_sec,
        "metrics_boot_delay_remaining_sec": round(max(0.0, delay_sec - elapsed), 1),
    }


def get_host_resource_snapshot(*, ttl_sec: Optional[float] = None, force: bool = False) -> Dict[str, Any]:
    """
    Снимок CPU / RAM / диска для диагностики и адаптации.
    Результат кэшируется (по умолчанию RESOURCE_METRICS_TTL_SEC или 8 с), чтобы не дергать psutil на каждом сообщении.
    """
    global _cache, _cache_ts, _last_pressure_level
    if force:
        _cache = {}
        _cache_ts = 0.0
    if ttl_sec is None:
        ttl_sec = max(1.0, float(os.getenv("RESOURCE_METRICS_TTL_SEC", "8")))
    now = time.monotonic()
    if _cache and (now - _cache_ts) < ttl_sec:
        out = dict(_cache)
        out["cached"] = True
        return out

    if psutil is None:
        out = {
            "available": False,
            "error": "psutil_not_installed",
            "pressure": {"level": "unknown", "reasons": ["psutil_missing"]},
            "ts": time.time(),
        }
        _cache = {k: v for k, v in out.items() if k != "cached"}
        _cache_ts = now
        return out

    if _in_metrics_boot_delay():
        out = _snapshot_during_metrics_boot_delay()
        _cache = dict(out)
        _cache_ts = now
        return dict(out)

    try:
        cpu = _read_cpu_percent()
        _cpu_warmed = True
        vm = psutil.virtual_memory()
        mem_pct = float(vm.percent)
        mem_used = vm.used
        mem_total = vm.total
        disks: List[Dict[str, Any]] = []

        def _root_path() -> Path:
            if os.name == "nt":
                return Path(os.environ.get("SystemDrive", "C:") + "\\")
            return Path("/")

        for label, p in (
            ("root", _root_path()),
            ("project", _project_root()),
            ("data", _project_root() / "data"),
        ):
            du = _disk_usage_for_path(p)
            if du:
                du["label"] = label
                disks.append(du)

        pressure = _evaluate_pressure(cpu, mem_pct, disks)
        try:
            load1, load5, load15 = os.getloadavg()
            load_avg = {"1m": round(load1, 2), "5m": round(load5, 2), "15m": round(load15, 2)}
        except (AttributeError, OSError):
            load_avg = {}

        out = {
            "available": True,
            "cpu_percent": round(cpu, 2),
            "memory": {
                "percent": round(mem_pct, 2),
                "used_mb": round(mem_used / (1024**2), 1),
                "total_mb": round(mem_total / (1024**2), 1),
            },
            "disk": disks,
            "load_avg": load_avg,
            "pressure": pressure,
            "ts": time.time(),
            "adaptation_hints": _adaptation_hints(pressure),
            "cpu_boot_grace_active": _in_cpu_boot_grace(),
            "metrics_boot_delay_active": False,
        }
        if pressure["level"] != _last_pressure_level:
            _last_pressure_level = pressure["level"]
            try:
                from core.monitoring import MONITOR

                MONITOR.inc("host_resource_pressure_changes_total")
                if pressure["level"] == "critical":
                    MONITOR.inc("host_resource_pressure_critical_total")
            except Exception as e:
                logger.debug('%s optional failed: %s', 'host_resources', e, exc_info=True)
        _cache = dict(out)
        _cache_ts = now
        return dict(out)
    except Exception as e:
        logger.warning("host_resources snapshot failed: %s", e)
        out = {
            "available": False,
            "error": str(e),
            "pressure": {"level": "unknown", "reasons": [f"collect_error:{e}"]},
            "ts": time.time(),
        }
        _cache = {k: v for k, v in out.items() if k != "cached"}
        _cache_ts = now
        return out


def _adaptation_hints(pressure: Dict[str, Any]) -> List[str]:
    """Рекомендации для политик / планировщика (без автоматического pip и т.д.)."""
    lvl = pressure.get("level") or "ok"
    hints: List[str] = []
    if lvl == "critical":
        hints.append("prefer_minimal_modules")
        hints.append("defer_heavy_rag_and_vision")
        hints.append("shorten_context_budget")
    elif lvl == "warn":
        hints.append("reduce_parallel_work")
        hints.append("prefer_cached_responses_where_safe")
    return hints


def resource_pressure_degrades_system() -> bool:
    return _env_truthy("RESOURCE_PRESSURE_DEGRADES", "true")


def resource_pressure_escalation_enabled() -> bool:
    """Включена ли эскалация по давлению на хост (бывш. env-only флаг RESOURCE_PRESSURE_CRITICAL)."""
    return _env_truthy("RESOURCE_PRESSURE_CRITICAL", "true")


def resource_pressure_is_critical() -> bool:
    """Реальное критическое давление на хосте (не просто env-флаг)."""
    if not resource_pressure_escalation_enabled():
        return False
    snap = get_host_resource_snapshot()
    pr = snap.get("pressure") if isinstance(snap.get("pressure"), dict) else {}
    return str(pr.get("level") or "ok") == "critical"


def host_pressure_level() -> str:
    snap = get_host_resource_snapshot()
    pr = snap.get("pressure") if isinstance(snap.get("pressure"), dict) else {}
    return str(pr.get("level") or "ok")
