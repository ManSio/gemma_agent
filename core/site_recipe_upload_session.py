"""
Сессия загрузки site recipe через Telegram (только админы): батч JSON → валидация → запись в SITE_RECIPE_DIR.
Без режима по умолчанию — ложные срабатывания на обычные документы невозможны.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _session_key(user_id: str, chat_id: str) -> str:
    return f"{user_id}:{chat_id}"


def _ttl_sec() -> float:
    try:
        return max(60.0, float((os.getenv("SITE_RECIPE_UPLOAD_TTL_SEC") or "600").strip()))
    except ValueError:
        return 600.0


def _defer_ttl_sec() -> float:
    try:
        return max(60.0, float((os.getenv("SITE_RECIPE_UPLOAD_DEFER_TTL_SEC") or "1800").strip()))
    except ValueError:
        return 1800.0


@dataclass
class RecipeUploadSession:
    user_id: str
    chat_id: str
    until: float  # time.monotonic()
    items: List[Tuple[str, Dict[str, Any], str]] = field(default_factory=list)
    # (hostname, normalized recipe dict, original filename)


_SESSIONS: Dict[str, RecipeUploadSession] = {}
# token -> {user_id, chat_id, file_context, until}
_DEFER_NORMAL: Dict[str, Dict[str, Any]] = {}


def purge_stale() -> None:
    now = time.monotonic()
    dead = [k for k, s in _SESSIONS.items() if s.until < now]
    for k in dead:
        _SESSIONS.pop(k, None)
    ddead = [t for t, d in _DEFER_NORMAL.items() if float(d.get("until") or 0) < now]
    for t in ddead:
        rec = _DEFER_NORMAL.pop(t, None)
        if isinstance(rec, dict):
            fc = rec.get("file_context")
            if isinstance(fc, dict):
                lp = fc.get("local_path")
                if isinstance(lp, str) and lp:
                    try:
                        if os.path.isfile(lp):
                            os.remove(lp)
                    except OSError:
                        pass


def start_session(user_id: str, chat_id: str) -> None:
    purge_stale()
    k = _session_key(user_id, chat_id)
    _SESSIONS[k] = RecipeUploadSession(
        user_id=user_id,
        chat_id=chat_id,
        until=time.monotonic() + _ttl_sec(),
        items=[],
    )


def cancel_session(user_id: str, chat_id: str) -> None:
    _SESSIONS.pop(_session_key(user_id, chat_id), None)
    uid, cid = str(user_id), str(chat_id)
    drop = [
        t
        for t, d in list(_DEFER_NORMAL.items())
        if isinstance(d, dict) and str(d.get("user_id")) == uid and str(d.get("chat_id")) == cid
    ]
    for t in drop:
        rec = _DEFER_NORMAL.pop(t, None)
        if isinstance(rec, dict):
            fc = rec.get("file_context")
            if isinstance(fc, dict):
                lp = fc.get("local_path")
                if isinstance(lp, str) and lp:
                    try:
                        if os.path.isfile(lp):
                            os.remove(lp)
                    except OSError:
                        pass


def touch_session(user_id: str, chat_id: str) -> None:
    k = _session_key(user_id, chat_id)
    s = _SESSIONS.get(k)
    if s:
        s.until = time.monotonic() + _ttl_sec()


def get_session(user_id: str, chat_id: str) -> Optional[RecipeUploadSession]:
    purge_stale()
    s = _SESSIONS.get(_session_key(user_id, chat_id))
    if s and s.until >= time.monotonic():
        return s
    if s:
        _SESSIONS.pop(_session_key(user_id, chat_id), None)
    return None


def session_active(user_id: str, chat_id: str) -> bool:
    return get_session(user_id, chat_id) is not None


def max_batch() -> int:
    try:
        return max(1, min(50, int((os.getenv("SITE_RECIPE_UPLOAD_MAX_FILES") or "20").strip())))
    except ValueError:
        return 20


def append_item(user_id: str, chat_id: str, host: str, recipe: Dict[str, Any], fname: str) -> Tuple[bool, str]:
    s = get_session(user_id, chat_id)
    if not s:
        return False, "сессия не активна"
    if len(s.items) >= max_batch():
        return False, f"лимит файлов ({max_batch()})"
    touch_session(user_id, chat_id)
    s.items.append((host, dict(recipe), fname))
    return True, ""


def defer_register_normal(user_id: str, chat_id: str, file_context: Dict[str, Any]) -> str:
    purge_stale()
    token = secrets.token_hex(4)
    fc = dict(file_context)
    _DEFER_NORMAL[token] = {
        "user_id": str(user_id),
        "chat_id": str(chat_id),
        "file_context": fc,
        "until": time.monotonic() + _defer_ttl_sec(),
    }
    return token


def defer_pop_normal(token: str, user_id: str) -> Optional[Dict[str, Any]]:
    purge_stale()
    rec = _DEFER_NORMAL.pop((token or "").strip().lower(), None)
    if not isinstance(rec, dict):
        return None
    if str(rec.get("user_id") or "") != str(user_id):
        return None
    return rec.get("file_context") if isinstance(rec.get("file_context"), dict) else None


def extract_host(raw: Dict[str, Any], filename: str) -> str:
    h = str(raw.get("host") or "").strip().lower()
    if h and re.match(r"^[\w.\-]+$", h):
        return h[:200]
    base = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if not base.lower().endswith(".json"):
        return ""
    base = base[:-5].strip().lower()
    if base and re.match(r"^[\w.\-]+$", base):
        return base[:200]
    return ""


def try_parse_recipe_file(local_path: str, original_name: str) -> Tuple[bool, Dict[str, Any], str, str]:
    """
    Returns: ok, normalized_or_raw, error_message, host
    """
    try:
        max_b = max(1024, min(2 * 1024 * 1024, int((os.getenv("SITE_RECIPE_UPLOAD_MAX_BYTES") or "524288").strip())))
    except ValueError:
        max_b = 524288
    try:
        sz = os.path.getsize(local_path)
    except OSError as e:
        return False, {}, str(e), ""
    if sz > max_b:
        return False, {}, f"файл слишком большой ({sz} байт, лимит {max_b})", ""
    try:
        with open(local_path, "r", encoding="utf-8") as f:
            raw_text = f.read()
    except UnicodeDecodeError:
        return False, {}, "не UTF-8 текст", ""
    except OSError as e:
        return False, {}, str(e), ""
    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError as e:
        return False, {}, f"не JSON: {e}", ""
    if not isinstance(raw, dict):
        return False, {}, "корень JSON должен быть объектом", ""

    from core.site_recipe_engine import normalize_recipe

    ok, norm, err = normalize_recipe(raw)
    if not ok:
        return False, raw, err or "normalize_recipe failed", ""

    host = extract_host(raw, original_name)
    if not host:
        return False, raw, "не удалось определить host: укажите поле host в JSON или имя файла вида law-archive.example.com.json", ""

    out = dict(norm)
    out["host"] = host
    for k in ("stats", "version", "sample_url"):
        if k in raw and raw[k] is not None:
            out[k] = raw[k]
    return True, out, "", host
