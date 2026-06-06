from __future__ import annotations

import logging

import os
import shutil
import tempfile
import time
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional

_LOCK = Lock()
_PENDING: Dict[str, List[Dict[str, Any]]] = {}


logger = logging.getLogger(__name__)

def _pending_max_bucket() -> int:
    raw = (os.getenv("IMAGE_PENDING_MAX_PHOTOS") or "3").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 3
    return max(1, min(n, 4))


def _pending_ttl_sec() -> int:
    raw = (os.getenv("IMAGE_PENDING_TTL_SEC") or "300").strip()
    try:
        ttl = int(raw)
    except ValueError:
        ttl = 300
    return max(30, min(ttl, 3600))


def _key(user_id: str, chat_id: str) -> str:
    return f"{user_id}:{chat_id}"


def _runtime_dir() -> Path:
    root = Path(os.getenv("GEMMA_PROJECT_ROOT") or ".").resolve()
    return root / "data" / "runtime" / "pending_images"


def _cleanup_file(path: str) -> None:
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except Exception as e:
        logger.debug('%s optional failed: %s', 'user_image_pending', e, exc_info=True)
def register_pending_image(user_id: str, chat_id: str, file_context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(file_context, dict):
        return None
    if file_context.get("file_type") != "image":
        return None
    src = str(file_context.get("local_path") or "").strip()
    if not src or not Path(src).is_file():
        return None
    _runtime_dir().mkdir(parents=True, exist_ok=True)
    ext = Path(src).suffix or ".jpg"
    fd, tmp = tempfile.mkstemp(prefix="gemma_pending_img_", suffix=ext, dir=str(_runtime_dir()))
    os.close(fd)
    shutil.copy2(src, tmp)
    clone = dict(file_context)
    clone["local_path"] = tmp
    now = int(time.time())
    rec = {
        "file_context": clone,
        "created_at": now,
        "expires_at": now + _pending_ttl_sec(),
    }
    k = _key(str(user_id), str(chat_id))
    with _LOCK:
        bucket = _PENDING.get(k)
        if not isinstance(bucket, list):
            bucket = []
        bucket.append(rec)
        while len(bucket) > _pending_max_bucket():
            old = bucket.pop(0)
            old_fc = old.get("file_context") if isinstance(old.get("file_context"), dict) else {}
            _cleanup_file(str(old_fc.get("local_path") or ""))
        _PENDING[k] = bucket
    return rec


def pop_pending_image(user_id: str, chat_id: str) -> Optional[Dict[str, Any]]:
    rows = pop_pending_images(user_id, chat_id, limit=1)
    return rows[0] if rows else None


def pending_image_count(user_id: str, chat_id: str) -> int:
    """Число неистёкших pending-фото (без извлечения)."""
    now = int(time.time())
    k = _key(str(user_id), str(chat_id))
    with _LOCK:
        rows = _PENDING.get(k)
    if not isinstance(rows, list):
        return 0
    n = 0
    for rec in rows:
        if not isinstance(rec, dict):
            continue
        if int(rec.get("expires_at") or 0) < now:
            continue
        fc = rec.get("file_context") if isinstance(rec.get("file_context"), dict) else {}
        if str(fc.get("local_path") or "").strip():
            n += 1
    return n


def has_pending_image(user_id: str, chat_id: str) -> bool:
    """Есть ли неистёкшее pending-фото (без извлечения из очереди)."""
    now = int(time.time())
    k = _key(str(user_id), str(chat_id))
    with _LOCK:
        rows = _PENDING.get(k)
    if not isinstance(rows, list):
        return False
    for rec in rows:
        if not isinstance(rec, dict):
            continue
        if int(rec.get("expires_at") or 0) < now:
            continue
        fc = rec.get("file_context") if isinstance(rec.get("file_context"), dict) else {}
        if str(fc.get("local_path") or "").strip():
            return True
    return False


def clear_pending_images(user_id: str, chat_id: str) -> int:
    """Сбросить очередь pending-фото (новый проект / одно фото+подпись без multiref)."""
    rows = pop_pending_images(user_id, chat_id, limit=_pending_max_bucket())
    n = 0
    for fc in rows:
        if isinstance(fc, dict):
            _cleanup_file(str(fc.get("local_path") or ""))
            n += 1
    return n


def pop_pending_images(user_id: str, chat_id: str, *, limit: int = 2) -> List[Dict[str, Any]]:
    now = int(time.time())
    k = _key(str(user_id), str(chat_id))
    with _LOCK:
        rows = _PENDING.pop(k, None)
    if not isinstance(rows, list):
        return []
    valid: List[Dict[str, Any]] = []
    for rec in rows:
        if not isinstance(rec, dict):
            continue
        if int(rec.get("expires_at") or 0) < now:
            fc = rec.get("file_context") if isinstance(rec.get("file_context"), dict) else {}
            _cleanup_file(str(fc.get("local_path") or ""))
            continue
        fc = rec.get("file_context") if isinstance(rec.get("file_context"), dict) else None
        if isinstance(fc, dict):
            valid.append(dict(fc))
    if not valid:
        return []
    limit = max(1, min(int(limit), _pending_max_bucket()))
    # Return newest first for natural "latest + previous" behavior.
    return list(reversed(valid))[:limit]

