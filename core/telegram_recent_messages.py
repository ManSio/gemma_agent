"""
Кольцевой буфер последних сообщений чата (входящие + синтетические ответы бота)
для контекста багрепорта без доступа к истории Telegram API.
"""
from __future__ import annotations

from collections import deque
from threading import Lock
from typing import Any, Deque, Dict, List, Optional

_MAX_PER_CHAT = int(__import__("os").getenv("TELEGRAM_RECENT_MSG_BUFFER", "40") or "40")
_MAX_PER_CHAT = max(8, min(_MAX_PER_CHAT, 200))
_LOCK = Lock()
_BUFFERS: Dict[str, Deque[Dict[str, Any]]] = {}


def chat_buffer_key(message: Any) -> str:
    tid = getattr(message, "message_thread_id", None) or 0
    chat = getattr(message, "chat", None)
    cid = getattr(chat, "id", None) if chat is not None else None
    return f"{cid}:{tid}"


def _append(chat_key: str, snap: Dict[str, Any]) -> None:
    mid = snap.get("message_id")
    with _LOCK:
        dq = _BUFFERS.get(chat_key)
        if dq is None:
            dq = deque(maxlen=_MAX_PER_CHAT)
            _BUFFERS[chat_key] = dq
        if mid is not None and dq and dq[-1].get("message_id") == mid:
            dq[-1] = snap
        else:
            dq.append(snap)


def record_incoming_message(message: Any, text: str) -> None:
    """Зафиксировать входящее сообщение пользователя (или любое не-бот в группе)."""
    if message is None:
        return
    u = getattr(message, "from_user", None)
    if u is not None and getattr(u, "is_bot", False):
        return
    txt = (text or "").strip()
    if not txt:
        return
    chat = getattr(message, "chat", None)
    if chat is None:
        return
    snap: Dict[str, Any] = {
        "message_id": getattr(message, "message_id", None),
        "role": "user",
        "text_or_caption": txt[:12_000] if len(txt) <= 12_000 else txt[:11_997] + "...",
    }
    try:
        dt = getattr(message, "date", None)
        snap["date_iso"] = dt.isoformat() if dt is not None else None
    except Exception:
        snap["date_iso"] = None
    if u is not None:
        snap["from_user_id"] = getattr(u, "id", None)
        snap["from_username"] = getattr(u, "username", None)
    _append(chat_buffer_key(message), snap)


def record_bot_reply_text(message: Any, text: str) -> None:
    """Добавить ответ бота в хвост (после отправки в чат)."""
    if message is None:
        return
    txt = (text or "").strip()
    if not txt:
        return
    chat = getattr(message, "chat", None)
    if chat is None:
        return
    snap = {
        "message_id": None,
        "role": "bot",
        "text_or_caption": txt[:12_000] if len(txt) <= 12_000 else txt[:11_997] + "...",
        "date_iso": None,
    }
    _append(chat_buffer_key(message), snap)


def recent_tail_for_chat(message: Any, n: int = 3) -> List[Dict[str, Any]]:
    """Последние n записей буфера для чата сообщения (хронологический порядок)."""
    k = chat_buffer_key(message)
    n = max(1, min(int(n), 20))
    with _LOCK:
        dq = _BUFFERS.get(k)
        if not dq:
            return []
        return list(dq)[-n:]
