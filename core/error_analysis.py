"""
Append-only runtime error diagnostics for operators.

No automatic repair — logging / correlation only.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from core.error_model import make_error

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_DEFAULT_DIR = os.path.join(os.getcwd(), "data")


def _log_path() -> str:
    base = os.getenv("ERROR_ANALYSIS_DIR", _DEFAULT_DIR)
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "runtime_errors.jsonl")


def runtime_errors_log_path() -> str:
    """Абсолютный путь к runtime_errors.jsonl (каталог создаётся)."""
    return _log_path()


def runtime_errors_file_meta() -> Dict[str, Any]:
    """Метаданные файла журнала для /admin_logs (без чтения содержимого)."""
    path = _log_path()
    out: Dict[str, Any] = {"path": path, "exists": False, "size_bytes": 0, "mtime_utc": ""}
    try:
        if os.path.isfile(path):
            out["exists"] = True
            st = os.stat(path)
            out["size_bytes"] = int(st.st_size)
            out["mtime_utc"] = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except OSError:
        pass
    return out


def _event_ts_key(row: Dict[str, Any]) -> float:
    """Монотонный ключ времени для сортировки строк журнала (UTC)."""
    ts = row.get("ts")
    if isinstance(ts, (int, float)):
        return float(ts)
    s = str(ts or "").strip()
    if not s:
        return 0.0
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError, OSError):
        return 0.0


def _sort_events_chronological(rows: list[Dict[str, Any]]) -> None:
    """Сортировка по времени по возрастанию, при равенстве — по коду и тексту."""
    rows.sort(
        key=lambda r: (
            _event_ts_key(r),
            str(r.get("code", "")),
            str(r.get("message", ""))[:64],
        )
    )


def _read_last_nonempty_lines(path: str, max_lines: int) -> List[str]:
    """Последние max_lines непустых строк файла; один проход, O(max_lines) памяти."""
    cap = max(1, int(max_lines))
    dq: deque[str] = deque(maxlen=cap)
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s:
                    dq.append(s)
    except OSError:
        return []
    return list(dq)


def record_error_event(
    component: str,
    message: str,
    *,
    exc: Optional[BaseException] = None,
    extra: Optional[Dict[str, Any]] = None,
    severity: str = "error",
) -> None:
    """Record a single structured line for later analysis (no side effects on code)."""
    unified = make_error(
        code=str((extra or {}).get("code", "GENERIC_ERROR")),
        component=component,
        message=message,
        severity=severity,
        exc=exc,
        context=extra or {},
    )
    row: Dict[str, Any] = unified.to_dict()
    line = json.dumps(row, ensure_ascii=False, default=str)
    path = _log_path()
    try:
        with _lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())
    except Exception as e:
        logger.debug("error_analysis write failed: %s", e)


def read_recent_events(
    limit: int = 200,
    *,
    component: Optional[str] = None,
    scan_max_lines: Optional[int] = None,
) -> list[Dict[str, Any]]:
    """Последние события из журнала.

    Без component: читаются только последние max(limit, 1) строк файла (эффективно по памяти).
    С component: сканируется хвост из scan_max_lines (или RUNTIME_ERRORS_FILTER_SCAN_MAX) строк,
    затем отбираются записи с данным component (последние limit совпадений).
    """
    path = _log_path()
    if not os.path.isfile(path):
        return []
    comp = (component or "").strip().lower()
    if comp:
        raw_cap = int(
            scan_max_lines
            if scan_max_lines is not None
            else (os.getenv("RUNTIME_ERRORS_FILTER_SCAN_MAX", "50000") or "50000")
        )
    else:
        raw_cap = int(scan_max_lines) if scan_max_lines is not None else max(1, int(limit))
    try:
        raw_lines = _read_last_nonempty_lines(path, raw_cap)
        rows: list[Dict[str, Any]] = []
        for line in raw_lines:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
        if comp:
            rows = [r for r in rows if str(r.get("component", "")).strip().lower() == comp]
        chunk = rows[-max(1, int(limit)) :]
        _sort_events_chronological(chunk)
        return chunk
    except Exception:
        return []


def _severities_counted_for_resilience() -> set:
    """Только эти severities участвуют в порогах ResilienceController (см. RESILIENCE_ERROR_COUNT_SEVERITIES)."""
    raw = os.getenv("RESILIENCE_ERROR_COUNT_SEVERITIES", "error").strip().lower()
    out = {x.strip() for x in raw.split(",") if x.strip()}
    return out or {"error"}


def _resilience_error_max_age_hours() -> float:
    try:
        return max(0.0, float((os.getenv("RESILIENCE_ERROR_MAX_AGE_HOURS") or "48").strip()))
    except (TypeError, ValueError):
        return 48.0


def _resilience_error_exclude_substrings() -> tuple[str, ...]:
    """Шум для порогов safe-mode: внешние API, циклы safe_mode, флаги restart (не «поломка ядра»)."""
    raw = os.getenv("RESILIENCE_ERROR_EXCLUDE_MESSAGE_SUBSTR")
    if raw is None:
        default = (
            "safe_mode entered",
            "safe_mode cleared",
            "restart flag",
            "news_enrich",
            "news_item_search",
            "news_item_url_fetch",
            "news_digest_llm",
            "news_direct",
            "news_universal",
            "weather_direct",
            "weather_universal",
            "anomaly escalation",
        )
        return default
    parts = tuple(x.strip().lower() for x in raw.split(",") if x.strip())
    return parts if parts else ()


def _event_within_max_age(row: Dict[str, Any], *, max_age_h: float) -> bool:
    if max_age_h <= 0:
        return True
    ts = row.get("ts")
    if not ts:
        return True
    try:
        s = str(ts).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        cut = datetime.now(timezone.utc) - timedelta(hours=max_age_h)
        return dt >= cut
    except (ValueError, TypeError, OSError):
        return True


def _resilience_counts_event(row: Dict[str, Any]) -> bool:
    sev = str(row.get("severity", "error")).strip().lower()
    if sev not in _severities_counted_for_resilience():
        return False
    if not _event_within_max_age(row, max_age_h=_resilience_error_max_age_hours()):
        return False
    msg = str(row.get("message") or "").lower()
    for sub in _resilience_error_exclude_substrings():
        if sub and sub in msg:
            return False
    return True


def aggregate_error_stats(limit: int = 500, *, for_resilience: bool = False) -> Dict[str, Any]:
    rows = read_recent_events(limit=limit)
    sev_ok = _severities_counted_for_resilience()
    by_component: Dict[str, int] = {}
    by_code: Dict[str, int] = {}
    by_component_all: Dict[str, int] = {}
    by_code_all: Dict[str, int] = {}
    counted: list[Dict[str, Any]] = []
    excluded_resilience = 0
    for r in rows:
        comp = str(r.get("component", "unknown"))
        code = str(r.get("code", "GENERIC_ERROR"))
        by_component_all[comp] = by_component_all.get(comp, 0) + 1
        by_code_all[code] = by_code_all.get(code, 0) + 1
        sev = str(r.get("severity", "error")).strip().lower()
        if sev not in sev_ok:
            continue
        if for_resilience and not _resilience_counts_event(r):
            excluded_resilience += 1
            continue
        counted.append(r)
        by_component[comp] = by_component.get(comp, 0) + 1
        by_code[code] = by_code.get(code, 0) + 1
    out: Dict[str, Any] = {
        "total": len(counted),
        "total_all": len(rows),
        "by_component": by_component,
        "by_code": by_code,
        "by_component_all": by_component_all,
        "by_code_all": by_code_all,
    }
    if for_resilience:
        out["excluded_resilience_noise"] = excluded_resilience
        out["max_age_hours"] = _resilience_error_max_age_hours()
    return out
