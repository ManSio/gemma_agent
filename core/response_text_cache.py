"""
Кэш готовых текстовых ответов (экономия LLM): только безопасные случаи.

По умолчанию выкл. Вкл: BRAIN_RESPONSE_CACHE_ENABLED=true
Модули: BRAIN_RESPONSE_CACHE_MODULES=math,echo
Не кэшируем: reply/forward/вложения, чувствительные к свежести фразы, URL, ошибки math.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Set

from core.calc_slash import is_calc_slash_payload
from core.models import Output

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_store: Dict[str, Dict[str, Any]] = {}
_by_record: Dict[str, str] = {}
_insert_seq = 0.0

_TIME_SENSITIVE = re.compile(
    r"(курс\s|новост|погод|сегодня|сейчас|bitcoin|btc|доллар|евро|weather|который\s+час|utc\+|gmt)",
    re.IGNORECASE,
)


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def enabled() -> bool:
    return _truthy("BRAIN_RESPONSE_CACHE_ENABLED", False)


def ttl_sec() -> int:
    try:
        return max(60, min(604800, int(os.getenv("BRAIN_RESPONSE_CACHE_TTL_SEC", "3600"))))
    except ValueError:
        return 3600


def max_entries() -> int:
    try:
        return max(32, min(10000, int(os.getenv("BRAIN_RESPONSE_CACHE_MAX_ENTRIES", "500"))))
    except ValueError:
        return 500


def min_chars() -> int:
    try:
        return max(4, min(500, int(os.getenv("BRAIN_RESPONSE_CACHE_MIN_CHARS", "8"))))
    except ValueError:
        return 8


def max_chars() -> int:
    try:
        return max(50, min(8000, int(os.getenv("BRAIN_RESPONSE_CACHE_MAX_CHARS", "2000"))))
    except ValueError:
        return 2000


def show_badge() -> bool:
    return _truthy("BRAIN_RESPONSE_CACHE_SHOW_BADGE", False)


def allowed_modules() -> Set[str]:
    raw = (os.getenv("BRAIN_RESPONSE_CACHE_MODULES") or "math,echo").strip().lower()
    return {p.strip() for p in raw.split(",") if p.strip()}


def normalize_text(text: str) -> str:
    t = (text or "").strip().lower()
    return re.sub(r"\s+", " ", t)


def cache_key_hash(user_id: str, chat_id: str, norm: str) -> str:
    return hashlib.sha256(f"{user_id}\n{chat_id}\n{norm}".encode("utf-8")).hexdigest()


def record_id_from_key(key: str) -> str:
    return key[:16]


def _evict_one() -> None:
    global _store, _by_record
    if len(_store) <= max_entries():
        return
    oldest_k: Optional[str] = None
    oldest_ord: float = 1e30
    now = time.time()
    for k, v in _store.items():
        if float(v.get("expires", 0)) < now:
            oldest_k = k
            break
        o = float(v.get("_ord", 0))
        if o < oldest_ord:
            oldest_ord = o
            oldest_k = k
    if oldest_k and oldest_k in _store:
        rid = str(_store[oldest_k].get("record_id") or "")
        _store.pop(oldest_k, None)
        if rid:
            _by_record.pop(rid, None)


def _meta_blocks_cache(meta: Dict[str, Any]) -> bool:
    if not isinstance(meta, dict):
        return True
    if meta.get("telegram_reply_context") or meta.get("telegram_has_forward"):
        return True
    if meta.get("has_telegram_attachment"):
        return True
    if meta.get("file_context") or meta.get("document_intake") or meta.get("code_intake"):
        return True
    if meta.get("pending_doc_id"):
        return True
    return False


def should_skip_cache_read(meta: Dict[str, Any]) -> bool:
    if not isinstance(meta, dict):
        return True
    if meta.get("response_cache_skip_once"):
        meta.pop("response_cache_skip_once", None)
        return True
    return _meta_blocks_cache(meta)


def get_hit(user_id: str, chat_id: str, payload: str, meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Вернуть {text, module, record_id, replay_payload} или None."""
    if not enabled():
        return None
    if should_skip_cache_read(meta):
        return None
    t = normalize_text(payload)
    if len(t) < min_chars() or len(t) > max_chars():
        return None
    if _TIME_SENSITIVE.search(t):
        return None
    if "http://" in t or "https://" in t or "t.me/" in t:
        return None
    pl = (payload or "").strip()
    if pl.startswith("/") and not is_calc_slash_payload(pl):
        return None

    key = cache_key_hash(user_id, chat_id, t)
    now = time.time()
    with _lock:
        ent = _store.get(key)
        if not ent:
            return None
        if float(ent.get("expires", 0)) < now:
            rid = str(ent.get("record_id") or "")
            _store.pop(key, None)
            if rid:
                _by_record.pop(rid, None)
            return None
        return {
            "text": str(ent.get("text") or ""),
            "module": str(ent.get("module") or ""),
            "record_id": str(ent.get("record_id") or ""),
            "replay_payload": str(ent.get("replay_payload") or pl),
        }


def lookup_record(record_id: str) -> Optional[Dict[str, Any]]:
    rid = (record_id or "").strip().lower()
    if len(rid) != 16:
        return None
    now = time.time()
    with _lock:
        key = _by_record.get(rid)
        if not key:
            return None
        ent = _store.get(key)
        if not ent or float(ent.get("expires", 0)) < now:
            _by_record.pop(rid, None)
            _store.pop(key, None)
            return None
        return {
            "user_id": str(ent.get("user_id") or ""),
            "chat_id": str(ent.get("chat_id") or ""),
            "replay_payload": str(ent.get("replay_payload") or ""),
        }


def maybe_store(
    *,
    user_id: str,
    chat_id: str,
    replay_payload: str,
    input_meta: Dict[str, Any],
    module_name: str,
    outputs: List[Output],
) -> None:
    if not enabled():
        return
    im = dict(input_meta) if isinstance(input_meta, dict) else {}
    im.pop("response_cache_skip_once", None)
    if _meta_blocks_cache(im):
        return
    m0 = (module_name or "").strip().lower()
    if m0 not in allowed_modules():
        return
    if len(outputs) != 1:
        return
    o0 = outputs[0]
    if o0.type != "text":
        return
    body = str(o0.payload or "").strip()
    if not body:
        return
    meta = o0.meta if isinstance(o0.meta, dict) else {}
    if meta.get("error") or meta.get("fallback") or meta.get("hint"):
        return
    t = normalize_text(replay_payload)
    if len(t) < min_chars() or len(t) > max_chars():
        return
    if _TIME_SENSITIVE.search(t):
        return

    key = cache_key_hash(user_id, chat_id, t)
    rid = record_id_from_key(key)
    global _insert_seq
    with _lock:
        _insert_seq += 1.0
        _store[key] = {
            "text": body,
            "module": m0,
            "record_id": rid,
            "replay_payload": (replay_payload or "").strip(),
            "user_id": str(user_id),
            "chat_id": str(chat_id),
            "expires": time.time() + float(ttl_sec()),
            "_ord": _insert_seq,
        }
        _by_record[rid] = key
        while len(_store) > max_entries():
            _evict_one()


def format_answer_text(cached_text: str) -> str:
    if show_badge():
        return f"{cached_text}\n\n— ответ из кэша"
    return cached_text


def build_hit_keyboard(module: str, record_id: str):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    label = "🔄 Пересчитать" if (module or "").strip().lower() == "math" else "🔄 Без кэша"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=f"rc:r:{record_id}")]]
    )


def reset_for_tests() -> None:
    global _store, _by_record, _insert_seq
    with _lock:
        _store = {}
        _by_record = {}
        _insert_seq = 0.0
