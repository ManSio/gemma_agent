"""Парсинг /admin_bug и сериализация контекста Telegram для архива багрепорта."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# Снятие NL-триггера в начале сообщения, остаток — как аргументы /admin_bug.
_NL_BUG_PREFIX_RU = re.compile(
    r"(?is)^\s*(?:🐞\s*)?(?:(?:за)?фиксируй(?:те)?|зафиксировать)\s+баг\b[.:!»\"]*\s*",
)
_NL_BUG_PREFIX_EN = re.compile(
    r"(?is)^\s*(?:🐞\s*)?(?:bug\s+report|file\s+(?:a\s+)?bug|capture\s+bug)\b[.:!»\"]*\s*",
)

_NL_BUG_DETECT = re.compile(
    r"(?im)(?:"
    r"^\s*(?:🐞\s*)?(?:(?:за)?фиксируй(?:те)?|зафиксировать)\s+баг\b"
    r"|"
    r"^\s*(?:🐞\s*)?(?:bug\s+report|file\s+(?:a\s+)?bug|capture\s+bug)\b"
    r")",
)


def prose_wants_bug_report_capture(text: str) -> bool:
    """Фраза «зафиксируй баг» / bug report в начале строки (как операторская команда)."""
    t = (text or "").strip()
    if not t or t.startswith("/"):
        return False
    low = t.casefold()
    if low.startswith(
        (
            "зафиксируй баг",
            "зафиксируйте баг",
            "фиксируй баг",
            "фиксируйте баг",
            "зафиксировать баг",
            "🐞 зафиксируй баг",
            "🐞зафиксируй баг",
        )
    ):
        return True
    if _NL_BUG_DETECT.search(t):
        return True
    return False


def bug_nl_args_remainder(text: str) -> str:
    """Текст после NL-триггера — парсится как хвост /admin_bug (net, N, comp=…, заметка)."""
    t = (text or "").strip()
    if not t:
        return ""
    lines = t.splitlines()
    if lines:
        l0 = lines[0]
        l0_stripped = _NL_BUG_PREFIX_RU.sub("", l0, count=1).strip()
        if l0_stripped != l0.strip():
            return "\n".join([l0_stripped] + lines[1:]).strip()
        l0_stripped = _NL_BUG_PREFIX_EN.sub("", l0, count=1).strip()
        if l0_stripped != l0.strip():
            return "\n".join([l0_stripped] + lines[1:]).strip()
    t2 = _NL_BUG_PREFIX_RU.sub("", t, count=1).strip()
    if t2 != t:
        return t2
    t2 = _NL_BUG_PREFIX_EN.sub("", t, count=1).strip()
    return t2


def parse_admin_bug_command_args(args: Optional[str]) -> Tuple[bool, int, Optional[str], bool, Optional[str]]:
    """
    Возвращает: include_net, log_lines (1..100), component (или None), include_full_bundle, human_note.

    Примеры:
      /admin_bug
      /admin_bug net
      /admin_bug 60
      /admin_bug net comp=voice
      /admin_bug ожидал другой ответ
      /admin_bug net 50 comp=brain краткая заметка
    """
    try:
        default_n = int((os.getenv("ADMIN_BUG_LOG_LINES") or "80").strip())
    except ValueError:
        default_n = 80
    default_n = max(1, min(default_n, 100))

    tokens = (args or "").strip().split()
    include_net = False
    include_full_bundle = False
    log_n = default_n
    log_comp: Optional[str] = None
    net_set = frozenset({"net", "network", "online", "1", "true", "yes"})
    full_set = frozenset({"full", "bundle", "raw", "complete"})
    i = 0
    while i < len(tokens):
        t = tokens[i]
        tl = t.lower()
        if tl in net_set:
            include_net = True
            i += 1
            continue
        if tl in full_set:
            include_full_bundle = True
            i += 1
            continue
        if t.isdigit():
            log_n = max(1, min(int(t), 100))
            i += 1
            continue
        if tl.startswith("comp="):
            log_comp = tl.split("=", 1)[1].strip() or None
            i += 1
            continue
        break
    human_note = " ".join(tokens[i:]).strip() or None
    return include_net, log_n, log_comp, include_full_bundle, human_note


def _build_event_timeline(
    *,
    command_message_id: Optional[int],
    command_chat_id: Optional[int],
    reply_to: Any,
    parent: Any,
    recent_chat_tail: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []

    def _evt(kind: str, item: Any, fallback_text: Optional[str] = None) -> None:
        if item is None:
            return
        ser = serialize_message_for_bug(item)
        if not ser:
            return
        ev: Dict[str, Any] = {
            "kind": kind,
            "date_iso": ser.get("date_iso"),
            "message_id": ser.get("message_id"),
            "chat_id": ser.get("chat_id"),
            "from_user_id": ser.get("from_user_id"),
            "from_username": ser.get("from_username"),
            "is_bot": ser.get("is_bot"),
            "text_or_caption": ser.get("text_or_caption") or fallback_text,
        }
        events.append(ev)

    _evt("reply_parent", parent)
    _evt("reply_to", reply_to)
    for row in recent_chat_tail or []:
        if not isinstance(row, dict):
            continue
        events.append(
            {
                "kind": str(row.get("role") or "tail"),
                "date_iso": row.get("date_iso"),
                "message_id": row.get("message_id"),
                "chat_id": command_chat_id,
                "from_user_id": row.get("from_user_id"),
                "from_username": row.get("from_username"),
                "is_bot": row.get("role") == "bot",
                "text_or_caption": row.get("text_or_caption"),
            }
        )
    events.append(
        {
            "kind": "admin_bug_command",
            "date_iso": None,
            "message_id": command_message_id,
            "chat_id": command_chat_id,
            "from_user_id": None,
            "from_username": None,
            "is_bot": None,
            "text_or_caption": "/admin_bug",
        }
    )
    return events


def serialize_message_for_bug(m: Any) -> Optional[dict[str, Any]]:
    if m is None:
        return None
    txt = (getattr(m, "text", None) or getattr(m, "caption", None) or "")
    txt = str(txt).strip()
    if len(txt) > 12_000:
        txt = txt[:11_997] + "..."
    u = getattr(m, "from_user", None)
    chat = getattr(m, "chat", None)
    dt = getattr(m, "date", None)
    date_iso: Optional[str]
    try:
        date_iso = dt.isoformat() if dt is not None else None
    except Exception:
        date_iso = None
    return {
        "message_id": getattr(m, "message_id", None),
        "date_iso": date_iso,
        "chat_id": getattr(chat, "id", None) if chat is not None else None,
        "from_user_id": getattr(u, "id", None) if u is not None else None,
        "from_username": getattr(u, "username", None) if u is not None else None,
        "is_bot": getattr(u, "is_bot", None) if u is not None else None,
        "text_or_caption": txt or None,
    }


def build_bug_report_document(
    *,
    command_chat_id: Optional[int],
    command_message_id: Optional[int],
    reporter_user: Any,
    human_note: Optional[str],
    reply_to: Any,
    recent_chat_tail: Optional[List[Dict[str, Any]]] = None,
    capture_source: Optional[str] = None,
) -> dict[str, Any]:
    parent = None
    if reply_to is not None:
        parent = getattr(reply_to, "reply_to_message", None)
    out: dict[str, Any] = {
        "report_version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "reporter": serialize_message_for_bug(reporter_user),
        "command_chat_id": command_chat_id,
        "command_message_id": command_message_id,
        "human_note": human_note,
        "reply_to": serialize_message_for_bug(reply_to),
        "reply_parent": serialize_message_for_bug(parent),
        "reply_missing": reply_to is None,
    }
    if reply_to is None:
        out["hint"] = (
            "Ответьте реплаем на сообщение с багом (обычно на ответ бота). "
            "Без реплая в архив всё равно попадут диагностика и логи, но не будет привязки к сообщению."
        )
    tail = list(recent_chat_tail or [])[-5:]
    if tail:
        out["recent_chat_tail"] = tail
    out["event_timeline"] = _build_event_timeline(
        command_message_id=command_message_id,
        command_chat_id=command_chat_id,
        reply_to=reply_to,
        parent=parent,
        recent_chat_tail=tail,
    )
    if capture_source:
        out["capture_source"] = str(capture_source)
    return out
