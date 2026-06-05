"""
Лёгкие замеры для диагностики: не полноценный бенчмарк, а порядок величины
(удобно сравнивать Docker volume vs локальный диск, «диск умирает» vs «сеть»).
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _project_root() -> Path:
    r = os.getenv("PROJECT_ROOT", "").strip()
    return Path(r).resolve() if r else Path.cwd()


def _probe_bytes() -> int:
    try:
        n = int(os.getenv("DIAG_IO_PROBE_BYTES", "2097152"))
    except ValueError:
        n = 2_097_152
    return max(65_536, min(n, 16 * 1024 * 1024))


def run_storage_io_probe(
    *,
    base_dir: Optional[Path] = None,
    size_bytes: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Последовательная запись + fsync + чтение + удаление в data/runtime (или base_dir).
    """
    size = int(size_bytes or _probe_bytes())
    base = base_dir or (_project_root() / "data" / "runtime")
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return {"ok": False, "error": str(e), "path": str(base)}

    path = base / f".io_probe_{uuid.uuid4().hex}.tmp"
    buf = bytearray(size)
    out: Dict[str, Any] = {"ok": True, "bytes": size, "path": str(path)}

    try:
        t0 = time.perf_counter()
        with open(path, "wb", buffering=0) as f:
            f.write(buf)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError as e:
                out["fsync_note"] = str(e)
        t1 = time.perf_counter()
        with open(path, "rb") as f:
            _ = f.read()
        t2 = time.perf_counter()
        w_ms = (t1 - t0) * 1000.0
        r_ms = (t2 - t1) * 1000.0
        mb = size / (1024 * 1024)
        out["write_fsync_ms"] = round(w_ms, 2)
        out["read_ms"] = round(r_ms, 2)
        out["write_mbps"] = round(mb / (w_ms / 1000.0), 3) if w_ms > 0 else None
        out["read_mbps"] = round(mb / (r_ms / 1000.0), 3) if r_ms > 0 else None
    except OSError as e:
        out = {"ok": False, "error": str(e), "bytes": size, "path": str(path)}
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    return out


def collect_performance_snapshot() -> Dict[str, Any]:
    """
    Снимок для /admin_diagnostic: хост (force) + CPU с коротким интервалом + диск I/O проба.
    """
    from core.host_resources import get_host_resource_snapshot

    host = get_host_resource_snapshot(force=True)

    cpu_interval: Optional[float] = None
    try:
        import psutil  # type: ignore

        if psutil is not None:
            try:
                cpu_interval = float(psutil.cpu_percent(interval=0.25))
            except Exception as e:
                logger.debug("performance_probe cpu interval: %s", e)
    except ImportError:
        pass

    storage = run_storage_io_probe()

    return {
        "ts": time.time(),
        "host_resources": host,
        "cpu_percent_sample_250ms": round(cpu_interval, 2) if cpu_interval is not None else None,
        "storage_io_probe": storage,
        "hints": _hints(storage, host),
    }


def _hints(storage: Dict[str, Any], host: Dict[str, Any]) -> list[str]:
    hints: list[str] = []
    if not storage.get("ok"):
        hints.append(f"storage_probe_failed:{storage.get('error')}")
        return hints
    w = float(storage.get("write_fsync_ms") or 0)
    if w > 3000:
        hints.append("very_slow_disk_write_fsync_over_3s_check_docker_volume_or_nas")
    elif w > 800:
        hints.append("slow_disk_write_fsync_over_800ms")
    r = float(storage.get("read_ms") or 0)
    if r > 2000:
        hints.append("very_slow_disk_read_over_2s")
    elif r > 500:
        hints.append("slow_disk_read_over_500ms")

    if isinstance(host, dict) and host.get("available"):
        p = (host.get("pressure") or {}).get("level")
        if p in ("warn", "critical"):
            hints.append(f"host_pressure_{p}")
    return hints
