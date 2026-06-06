"""
Сессия правки одного изображения (один план / одна цепочка правок).

Отдельно от pending_images (очередь 2–3 фото для multiref).
См. docs/IMAGE_GEN_SESSIONS_RU.md
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional

_LOCK = Lock()
logger = logging.getLogger(__name__)


def _enabled() -> bool:
    raw = os.getenv("IMAGE_EDIT_SESSION_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _ttl_sec() -> int:
    raw = (os.getenv("IMAGE_EDIT_SESSION_TTL_SEC") or "3600").strip()
    try:
        ttl = int(raw)
    except ValueError:
        ttl = 3600
    return max(300, min(ttl, 86400))


def _store_dir() -> Path:
    root = Path(os.getenv("GEMMA_PROJECT_ROOT") or ".").resolve()
    d = root / "data" / "runtime" / "image_edit_sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _key(user_id: str, chat_id: str) -> str:
    return f"{user_id}:{chat_id}"


def _path_for(user_id: str, chat_id: str) -> Path:
    safe = _key(user_id, chat_id).replace(":", "_")
    return _store_dir() / f"{safe}.json"


def _read(user_id: str, chat_id: str) -> Dict[str, Any]:
    p = _path_for(user_id, chat_id)
    if not p.is_file():
        return {}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return doc if isinstance(doc, dict) else {}


def _write(user_id: str, chat_id: str, doc: Dict[str, Any]) -> None:
    p = _path_for(user_id, chat_id)
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=0), encoding="utf-8")


def clear_image_edit_session(user_id: str, chat_id: str) -> None:
    with _LOCK:
        p = _path_for(user_id, chat_id)
        try:
            p.unlink(missing_ok=True)
        except Exception as e:
            logger.debug("clear_image_edit_session: %s", e)


def bind_image_input(user_id: str, chat_id: str, local_path: str) -> None:
    if not _enabled():
        return
    path = (local_path or "").strip()
    if not path or not Path(path).is_file():
        return
    now = int(time.time())
    with _LOCK:
        doc = _read(user_id, chat_id)
        doc.update(
            {
                "input_path": path,
                "updated_at": now,
                "expires_at": now + _ttl_sec(),
            }
        )
        _write(user_id, chat_id, doc)


def bind_image_output(user_id: str, chat_id: str, local_path: str) -> None:
    if not _enabled():
        return
    path = (local_path or "").strip()
    if not path or not Path(path).is_file():
        return
    now = int(time.time())
    with _LOCK:
        doc = _read(user_id, chat_id)
        doc.update(
            {
                "output_path": path,
                "updated_at": now,
                "expires_at": now + _ttl_sec(),
            }
        )
        _write(user_id, chat_id, doc)


def get_image_edit_session(user_id: str, chat_id: str) -> Optional[Dict[str, Any]]:
    if not _enabled():
        return None
    with _LOCK:
        doc = _read(user_id, chat_id)
    if not doc:
        return None
    exp = int(doc.get("expires_at") or 0)
    if exp and exp < int(time.time()):
        clear_image_edit_session(user_id, chat_id)
        return None
    return dict(doc)


def file_context_for_session_edit(user_id: str, chat_id: str) -> Optional[Dict[str, Any]]:
    """Один референс для текстовой правки («переделай») без нового фото."""
    doc = get_image_edit_session(user_id, chat_id)
    if not doc:
        return None
    for key in ("output_path", "input_path"):
        p = str(doc.get(key) or "").strip()
        if p and Path(p).is_file():
            return {
                "file_type": "image",
                "local_path": p,
                "original_name": Path(p).name,
                "image_edit_session_ref": key,
            }
    return None
