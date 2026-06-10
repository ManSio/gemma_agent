"""Журнал блокировок gate для последующего review (C1)."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def misses_log_enabled() -> bool:
    raw = os.getenv("HEURISTIC_MISSES_LOG_ENABLED", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _log_path() -> Path:
    root = (os.getenv("GEMMA_PROJECT_ROOT") or ".").strip() or "."
    custom = (os.getenv("HEURISTIC_MISSES_LOG_PATH") or "").strip()
    if custom:
        p = Path(custom)
        return p if p.is_absolute() else Path(root) / p
    return Path(root) / "data" / "runtime" / "heuristic_misses.jsonl"


def _hash_user_id(user_id: str) -> Optional[str]:
    value = str(user_id or "").strip()
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def record_heuristic_miss(
    *,
    rule_id: str,
    verdict: str,
    reason: str,
    user_text: str,
    topic_current: str = "",
    user_id: str = "",
) -> None:
    if not misses_log_enabled():
        return
    if verdict not in ("blocked", "uncertain"):
        return
    path = _log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        row: Dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "rule_id": str(rule_id or ""),
            "verdict": verdict,
            "reason": str(reason or ""),
            "text_len": len((user_text or "").strip()),
            "text_excerpt_redacted": True,
            "topic_current": (topic_current or "").strip() or None,
            "user_id_hash": _hash_user_id(user_id),
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.debug("heuristic_misses log: %s", e)
