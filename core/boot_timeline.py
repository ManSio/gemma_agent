"""
Метки времени запуска процесса (от первого импорта модуля до polling).
Нужны, чтобы сопоставлять «бот тупит» с конкретной фазой бутa без догадок.
"""
from __future__ import annotations

import logging

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

_t0_perf = time.perf_counter()
_t0_wall = time.time()
_marks: List[Dict[str, Any]] = []
_boot_state: Dict[str, Any] = {}


logger = logging.getLogger(__name__)

def _runtime_dir() -> Path:
    base = (os.getenv("BEHAVIOR_DATA_DIR") or "").strip() or os.path.join(os.getcwd(), "data")
    p = Path(base) / "runtime"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _boot_state_path() -> Path:
    return _runtime_dir() / "boot_state.json"


def _init_boot_state() -> Dict[str, Any]:
    path = _boot_state_path()
    prev: Dict[str, Any] = {}
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                prev = raw
        except Exception:
            prev = {}
    now_iso = datetime.fromtimestamp(_t0_wall, tz=timezone.utc).isoformat()
    now = {
        "last_start_epoch": float(_t0_wall),
        "last_start_utc": now_iso,
        "previous_start_epoch": prev.get("last_start_epoch"),
        "previous_start_utc": prev.get("last_start_utc"),
        "restart_detected": bool(prev.get("last_start_epoch")),
        "boot_count": int(prev.get("boot_count") or 0) + 1,
    }
    try:
        path.write_text(json.dumps(now, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.debug('%s optional failed: %s', 'boot_timeline', e, exc_info=True)
    return now


_boot_state = _init_boot_state()


def mark_boot(name: str, **extra: Any) -> None:
    """Добавить метку; delta_ms — миллисекунды от старта процесса (perf_counter)."""
    _marks.append(
        {
            "name": name,
            "delta_ms": round((time.perf_counter() - _t0_perf) * 1000.0, 3),
            "utc": datetime.now(timezone.utc).isoformat(),
            **extra,
        }
    )


def boot_timeline_snapshot() -> Dict[str, Any]:
    return {
        "origin_wall_epoch": _t0_wall,
        "origin_utc": datetime.fromtimestamp(_t0_wall, tz=timezone.utc).isoformat(),
        "marks": list(_marks),
        "last_delta_ms": _marks[-1]["delta_ms"] if _marks else 0.0,
        "boot_state": dict(_boot_state),
    }


def process_uptime_seconds(now_epoch: float | None = None) -> float:
    """Секунды аптайма текущего процесса от первого импорта boot_timeline."""
    now = time.time() if now_epoch is None else float(now_epoch)
    return max(0.0, now - float(_t0_wall))
