"""Журнал жалоб пользователя (/rate -1, явные сбои) для админ-диагностики."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _journal_path() -> Path:
    root = (os.getenv("GEMMA_PROJECT_ROOT") or ".").strip() or "."
    p = Path(root) / "data" / "runtime" / "user_issues.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def record_user_issue(
    *,
    user_id: str,
    score: int,
    source: str,
    user_text: str = "",
    assistant_excerpt: str = "",
    intent: str = "",
    module: str = "",
    skill: str = "",
    correction: str = "",
    category: str = "feedback",
) -> None:
    raw = (os.getenv("USER_ISSUE_JOURNAL_ENABLED") or "true").strip().lower()
    if raw not in {"1", "true", "yes", "on"}:
        return
    row: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "user_id": str(user_id or ""),
        "score": int(score),
        "source": source,
        "category": category,
        "intent": intent,
        "module": module,
        "skill": skill or None,
        "user_text": (user_text or "")[:500],
        "assistant_excerpt": (assistant_excerpt or "")[:800],
        "correction": (correction or "")[:400],
    }
    try:
        with open(_journal_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.debug("user_issue_journal: %s", e)


def persist_last_location(persisted: Dict[str, Any], location: Dict[str, Any]) -> Dict[str, Any]:
    """Сохранить последнюю геометку в dialogue_state для «что рядом» без повторной отправки."""
    if not isinstance(persisted, dict) or not isinstance(location, dict):
        return persisted
    ds = dict(persisted.get("dialogue_state") or {})
    try:
        lat = float(location.get("latitude"))
        lon = float(location.get("longitude"))
    except (TypeError, ValueError):
        return persisted
    ds["last_telegram_location"] = {
        "latitude": lat,
        "longitude": lon,
        "display_name": str(location.get("display_name") or ""),
    }
    persisted["dialogue_state"] = ds
    return persisted
