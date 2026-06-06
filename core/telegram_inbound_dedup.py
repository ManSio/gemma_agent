"""Дедупликация повторных Telegram-апдейтов (один message_id / callback_id — один ход)."""
from __future__ import annotations

import os
import time
from threading import Lock
from typing import Dict, Optional, Tuple

_SEEN_LOCK = Lock()
_SEEN_MESSAGES: Dict[str, float] = {}
_SEEN_CALLBACKS: Dict[str, float] = {}


def _env_flag(name: str, default: bool = True) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _ttl_sec() -> float:
    try:
        return max(5.0, float((os.getenv("TELEGRAM_INBOUND_DEDUP_TTL_SEC") or "120").strip()))
    except ValueError:
        return 120.0


def _prune(store: Dict[str, float], now: float) -> None:
    if len(store) <= 500:
        return
    cutoff = now - _ttl_sec() * 4
    for k in list(store.keys()):
        if store[k] < cutoff:
            del store[k]


def should_skip_duplicate_message(chat_id: str, message_id: Optional[int]) -> bool:
    """
    Повторная доставка того же message_id в чате (двойной polling / двойной handler).
  Возвращает True, если этот апдейт уже обрабатывали в окне TTL.
    """
    if not _env_flag("TELEGRAM_INBOUND_DEDUP_ENABLED", True):
        return False
    if message_id is None:
        return False
    try:
        mid = int(message_id)
    except (TypeError, ValueError):
        return False
    key = f"m:{chat_id}:{mid}"
    now = time.monotonic()
    ttl = _ttl_sec()
    with _SEEN_LOCK:
        prev = _SEEN_MESSAGES.get(key)
        _SEEN_MESSAGES[key] = now
        _prune(_SEEN_MESSAGES, now)
    return prev is not None and (now - prev) < ttl


def should_skip_duplicate_callback(callback_query_id: Optional[str]) -> bool:
    if not _env_flag("TELEGRAM_INBOUND_DEDUP_ENABLED", True):
        return False
    cid = (str(callback_query_id) if callback_query_id is not None else "").strip()
    if not cid:
        return False
    key = f"cb:{cid}"
    now = time.monotonic()
    ttl = _ttl_sec()
    with _SEEN_LOCK:
        prev = _SEEN_CALLBACKS.get(key)
        _SEEN_CALLBACKS[key] = now
        _prune(_SEEN_CALLBACKS, now)
    return prev is not None and (now - prev) < ttl


def reset_for_tests() -> None:
    with _SEEN_LOCK:
        _SEEN_MESSAGES.clear()
        _SEEN_CALLBACKS.clear()
