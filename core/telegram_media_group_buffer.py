"""
Буфер входящих альбомов Telegram (media_group_id).

Каждое фото альбома приходит отдельным апдейтом; собираем 1–10 шт., затем кладём в pending.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from threading import Lock
from typing import Any, Awaitable, Callable, Dict, List, Optional

_LOCK = Lock()
_BUCKETS: Dict[str, Dict[str, Any]] = {}
_TASKS: Dict[str, asyncio.Task] = {}
logger = logging.getLogger(__name__)

FlushCallback = Callable[[str, str, str, List[Dict[str, Any]]], Awaitable[None]]


def _collect_ms() -> int:
    raw = (os.getenv("TELEGRAM_MEDIA_GROUP_COLLECT_MS") or "1200").strip()
    try:
        ms = int(raw)
    except ValueError:
        ms = 1200
    return max(400, min(ms, 5000))


def _bucket_key(chat_id: str, media_group_id: str) -> str:
    return f"{chat_id}:{media_group_id}"


def media_group_buffer_enabled() -> bool:
    raw = os.getenv("TELEGRAM_MEDIA_GROUP_BUFFER_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on"}


async def offer_media_group_photo(
    *,
    user_id: str,
    chat_id: str,
    media_group_id: str,
    file_context: Dict[str, Any],
    on_flush: FlushCallback,
) -> bool:
    """
    True — фото принято в буфер, полный pipeline для этого апдейта не нужен.
    """
    if not media_group_buffer_enabled():
        return False
    mg = str(media_group_id or "").strip()
    if not mg or not isinstance(file_context, dict) or not file_context.get("local_path"):
        return False

    key = _bucket_key(chat_id, mg)
    now = time.monotonic()
    with _LOCK:
        bucket = _BUCKETS.get(key)
        if not isinstance(bucket, dict):
            bucket = {
                "items": [],
                "user_id": str(user_id),
                "chat_id": chat_id,
                "media_group_id": mg,
            }
            _BUCKETS[key] = bucket
        items = bucket.setdefault("items", [])
        if isinstance(items, list):
            items.append(dict(file_context))
        bucket["last_mono"] = now

        old_task = _TASKS.get(key)
        if old_task and not old_task.done():
            old_task.cancel()

        loop = asyncio.get_running_loop()
        _TASKS[key] = loop.create_task(
            _flush_after_delay(key, on_flush),
            name=f"media_group_flush_{mg[:8]}",
        )
    return True


async def _flush_after_delay(key: str, on_flush: FlushCallback) -> None:
    try:
        await asyncio.sleep(_collect_ms() / 1000.0)
    except asyncio.CancelledError:
        return
    items: List[Dict[str, Any]] = []
    mg = ""
    uid = ""
    cid = ""
    with _LOCK:
        bucket = _BUCKETS.pop(key, None)
        _TASKS.pop(key, None)
    if isinstance(bucket, dict):
        raw_items = bucket.get("items")
        if isinstance(raw_items, list):
            items = [dict(x) for x in raw_items if isinstance(x, dict) and x.get("local_path")]
        mg = str(bucket.get("media_group_id") or "")
        uid = str(bucket.get("user_id") or "")
        cid = str(bucket.get("chat_id") or "")
    if not items or not uid or not cid:
        return
    try:
        await on_flush(uid, cid, mg, items)
    except Exception as e:
        logger.warning("[media_group_buffer] flush failed: %s", e)
