"""Alert: залипший recent_fingerprint в turns.jsonl (Phase 0.4)."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.monitoring import MONITOR

logger = logging.getLogger(__name__)


def fingerprint_stall_minutes() -> float:
    """Порог залипания fingerprint (минуты)."""
    raw = (os.getenv("TURN_FINGERPRINT_STALL_MINUTES") or "5").strip()
    try:
        return max(1.0, min(120.0, float(raw)))
    except ValueError:
        return 5.0


def fingerprint_alert_enabled() -> bool:
    """Включён ли stall-alert."""
    raw = os.getenv("TURN_FINGERPRINT_ALERT_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_ts(raw: Any) -> Optional[datetime]:
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _turns_path() -> Path:
    from core.turn_observer import log_path

    return log_path()


def scan_fingerprint_stalls(
    *,
    path: Optional[Path] = None,
    limit: int = 500,
    stall_minutes: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Найти user_id с одним fingerprint дольше порога."""
    if not fingerprint_alert_enabled():
        return []
    p = path or _turns_path()
    if not p.is_file():
        return []
    threshold = stall_minutes if stall_minutes is not None else fingerprint_stall_minutes()
    cutoff_delta = timedelta(minutes=threshold)
    now = datetime.now(timezone.utc)

    rows: List[Dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines()[-max(50, limit) :]:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        if row.get("type") in ("scenario", "pre_send"):
            continue
        rows.append(row)

    by_chat: Dict[str, List[Tuple[datetime, str, Dict[str, Any]]]] = {}
    for row in rows:
        fp = str(row.get("recent_fingerprint") or "").strip()
        if not fp:
            continue
        ts = _parse_ts(row.get("ts"))
        if ts is None:
            continue
        uid = str(row.get("user_id") or "")
        gid = str(row.get("group_id") or "")
        key = f"{uid}:{gid}" if gid else uid
        by_chat.setdefault(key, []).append((ts, fp, row))

    alerts: List[Dict[str, Any]] = []
    for chat_key, events in by_chat.items():
        events.sort(key=lambda x: x[0])
        run_fp = ""
        run_start: Optional[datetime] = None
        run_count = 0
        last_row: Dict[str, Any] = {}
        for ts, fp, row in events:
            if fp == run_fp and run_fp:
                run_count += 1
                last_row = row
            else:
                if run_fp and run_start and run_count >= 2:
                    span = (events[-1][0] if events else ts) - run_start
                    if span >= cutoff_delta:
                        alerts.append(
                            {
                                "chat_key": chat_key,
                                "fingerprint": run_fp,
                                "turns": run_count,
                                "span_minutes": round(span.total_seconds() / 60.0, 1),
                                "since": run_start.isoformat(),
                                "last_trace_id": str(last_row.get("trace_id") or "")[:64],
                            }
                        )
                run_fp = fp
                run_start = ts
                run_count = 1
                last_row = row
        if run_fp and run_start and run_count >= 2:
            span = events[-1][0] - run_start
            if span >= cutoff_delta:
                alerts.append(
                    {
                        "chat_key": chat_key,
                        "fingerprint": run_fp,
                        "turns": run_count,
                        "span_minutes": round(span.total_seconds() / 60.0, 1),
                        "since": run_start.isoformat(),
                        "last_trace_id": str(last_row.get("trace_id") or "")[:64],
                    }
                )

    if alerts:
        MONITOR.inc("turn_fingerprint_stall_alert_total")
        for _ in alerts:
            MONITOR.inc("turn_fingerprint_stall_hit_total")
    return alerts
