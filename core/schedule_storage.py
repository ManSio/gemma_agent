"""
Единое хранилище расписания: data/schedule/user_schedules.json.

Раньше plugin slash писал schedules.json — миграция при первом load (идемпотентно).
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from core.json_atomic import atomic_write_json, read_json_file

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_MIGRATED_MARKER = ".migrated_from_plugin_schedules"


def _root() -> Path:
    raw = (os.getenv("GEMMA_PROJECT_ROOT") or ".").strip() or "."
    return Path(raw)


def canonical_path() -> Path:
    p = _root() / "data" / "schedule" / "user_schedules.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def legacy_plugin_path() -> Path:
    return _root() / "data" / "schedule" / "schedules.json"


def _marker_path() -> Path:
    return canonical_path().parent / _MIGRATED_MARKER


def _inner_schedule(user_row: Any) -> Dict[str, Any]:
    if not isinstance(user_row, dict):
        return {}
    if isinstance(user_row.get("schedule"), dict):
        return dict(user_row["schedule"])
    return dict(user_row)


def _merge_user_rows(existing: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(existing)
    for k, v in incoming.items():
        if k in ("user_id", "created_at", "updated_at", "_meta"):
            continue
        if k not in out:
            out[k] = v
        elif isinstance(out[k], list) and isinstance(v, list):
            out[k] = out[k] + v
        elif isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = {**out[k], **v}
        else:
            out[k] = v
    return out


def migrate_legacy_plugin_file() -> int:
    """Перенос schedules.json → user_schedules.json. Возвращает число uid в legacy-файле."""
    if _marker_path().is_file():
        return 0
    legacy = legacy_plugin_path()
    if not legacy.is_file():
        _marker_path().write_text(datetime.now().isoformat(), encoding="utf-8")
        return 0
    merged = load_all(migrate=False)
    try:
        raw = json.loads(legacy.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("[schedule_storage] legacy read failed")
        return 0
    if not isinstance(raw, dict):
        _marker_path().write_text(datetime.now().isoformat(), encoding="utf-8")
        return 0
    changed = False
    for uid, row in raw.items():
        key = str(uid).strip()
        inner = _inner_schedule(row)
        if not key or not inner:
            continue
        if key in merged:
            new_row = _merge_user_rows(merged[key], inner)
            if new_row != merged[key]:
                merged[key] = new_row
                changed = True
        else:
            merged[key] = inner
            changed = True
    if changed:
        save_all(merged)
    bak = legacy.with_suffix(".json.bak.migrated")
    try:
        legacy.replace(bak)
    except OSError:
        logger.warning("[schedule_storage] could not rename legacy to %s", bak)
    _marker_path().write_text(datetime.now().isoformat(), encoding="utf-8")
    logger.info("[schedule_storage] migrated legacy schedules.json (%s uid)", len(raw))
    return len(raw)


def load_all(*, migrate: bool = True) -> Dict[str, Dict[str, Any]]:
    if migrate:
        migrate_legacy_plugin_file()
    with _lock:
        raw = read_json_file(canonical_path(), {})
    return raw if isinstance(raw, dict) else {}


def save_all(data: Dict[str, Dict[str, Any]]) -> bool:
    with _lock:
        return atomic_write_json(canonical_path(), data)


def get_user(user_id: str) -> Dict[str, Any]:
    return dict(load_all().get(str(user_id).strip(), {}) or {})


def set_user(user_id: str, schedule_data: Dict[str, Any]) -> bool:
    uid = str(user_id).strip()
    if not uid:
        return False
    all_data = load_all()
    row = dict(all_data.get(uid) or {})
    row = _merge_user_rows(row, schedule_data)
    row["updated_at"] = datetime.now().isoformat(timespec="seconds")
    if "created_at" not in row:
        row["created_at"] = row["updated_at"]
    all_data[uid] = row
    return save_all(all_data)


def get_user_plugin_view(user_id: str) -> Dict[str, Any]:
    """Формат plugin: {user_id, schedule, created_at}."""
    uid = str(user_id).strip()
    inner = get_user(uid)
    if not inner:
        return {}
    return {
        "user_id": uid,
        "schedule": inner,
        "created_at": inner.get("created_at", ""),
    }


def set_user_from_plugin(user_id: str, schedule_data: Dict[str, Any]) -> bool:
    uid = str(user_id).strip()
    all_data = load_all()
    row = dict(all_data.get(uid) or {})
    merged = _merge_user_rows(row, schedule_data)
    merged["updated_at"] = datetime.now().isoformat(timespec="seconds")
    if "created_at" not in merged:
        merged["created_at"] = merged["updated_at"]
    all_data[uid] = merged
    return save_all(all_data)
