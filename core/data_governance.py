from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from core.error_analysis import _log_path, record_error_event


class DataGovernance:
    def __init__(self) -> None:
        self.retention_days_logs = int(os.getenv("RETENTION_LOG_DAYS", "30"))
        self.retention_days_behavior = int(os.getenv("RETENTION_BEHAVIOR_DAYS", "90"))
        self.redact_keys = {"token", "password", "api_key", "authorization", "secret"}

    def redact(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for k, v in (payload or {}).items():
            if k.lower() in self.redact_keys:
                out[k] = "***REDACTED***"
            elif isinstance(v, dict):
                out[k] = self.redact(v)
            else:
                out[k] = v
        return out

    def classify_record(self, row: Dict[str, Any]) -> str:
        c = str((row or {}).get("component", "")).lower()
        if "user_facts" in c or "behavior" in c:
            return "behavior"
        return "log"

    def purge_runtime_logs(self, *, full: bool = False) -> Dict[str, Any]:
        """Удаляет строки старше RETENTION_LOG_DAYS или при full=True очищает весь журнал (сброс счётчика для resilience)."""
        path = _log_path()
        mode = "full" if full else "retention"
        if not os.path.isfile(path):
            return {"ok": True, "removed": 0, "kept": 0, "mode": mode}
        try:
            if full:
                removed = 0
                with open(path, "r", encoding="utf-8") as f:
                    for ln in f:
                        if ln.strip():
                            removed += 1
                with open(path, "w", encoding="utf-8"):
                    pass
                return {"ok": True, "removed": removed, "kept": 0, "mode": mode}
            cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days_logs)
            kept: List[str] = []
            removed = 0
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            for ln in lines:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    import json

                    row = json.loads(ln)
                    ts = row.get("ts")
                    dt = datetime.fromisoformat(ts) if ts else None
                    if not dt or dt.tzinfo is None:
                        kept.append(ln + "\n")
                        continue
                    if dt < cutoff:
                        removed += 1
                    else:
                        kept.append(ln + "\n")
                except Exception:
                    kept.append(ln + "\n")
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(kept)
            return {"ok": True, "removed": removed, "kept": len(kept), "mode": mode}
        except Exception as e:
            record_error_event("data_governance", "purge_runtime_logs failed", exc=e)
            return {"ok": False, "error": str(e), "mode": mode}


DG = DataGovernance()
