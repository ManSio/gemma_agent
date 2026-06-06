"""
Кольцевой буфер сообщений группы (только то, что бот видит в апдейтах).

Telegram Bot API не отдаёт историю чата задним числом — храним последние N реплик
на диске, чтобы в промпт попадала компактная выжимка без полного дубляжа диалога с ботом.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from aiogram.types import Message

from core.safe_paths import resolve_under

logger = logging.getLogger(__name__)

DEFAULT_BASE = os.path.join(os.getcwd(), "data")
_LOCK = threading.Lock()

_COMMITMENT_RE = re.compile(
    r"(?i)(запомни|напомни|не\s*забудь|напоминай|сохрани\s*это|запиши\s*себе|"
    r"ты\s+должен|надо\s+сделать|сделай\s+к\s*:|обязательно\s+сделай)",
)


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _safe_chat_file_id(chat_id: str) -> str:
    s = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(chat_id))[:128]
    return s or "unknown"


def _dir() -> str:
    base = os.getenv("GROUP_TRANSCRIPT_DIR") or os.path.join(
        os.getenv("BEHAVIOR_DATA_DIR", DEFAULT_BASE), "group_transcripts"
    )
    os.makedirs(base, exist_ok=True)
    return base


def _path(group_id: str) -> str:
    return resolve_under(_dir(), f"g_{_safe_chat_file_id(group_id)}.json")


def max_roster_ids() -> int:
    try:
        return max(16, min(int(os.getenv("GROUP_ROSTER_MAX_IDS", "80")), 500))
    except ValueError:
        return 80


def _defaults() -> Dict[str, Any]:
    return {"entries": [], "commitments": [], "roster": {}}


def _load(group_id: str) -> Dict[str, Any]:
    path = _path(group_id)
    with _LOCK:
        if not os.path.isfile(path):
            return _defaults()
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                return _defaults()
            out = _defaults()
            ent = raw.get("entries")
            out["entries"] = ent if isinstance(ent, list) else []
            com = raw.get("commitments")
            out["commitments"] = com if isinstance(com, list) else []
            rs = raw.get("roster")
            out["roster"] = rs if isinstance(rs, dict) else {}
            return out
        except Exception as e:
            logger.debug("group_transcript load %s: %s", group_id, e)
            return _defaults()


def _save(group_id: str, data: Dict[str, Any]) -> None:
    path = _path(group_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _LOCK:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def _utc_iso(ts: int) -> str:
    """Человекочитаемое время для JSON (рядом с компактным Unix `t`)."""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _clip(s: str, max_len: int) -> str:
    t = (s or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(t) <= max_len:
        return t
    return t[: max_len - 1] + "…"


def _display_name(message: Message) -> str:
    u = message.from_user
    if not u:
        return "?"
    parts = [p for p in (u.first_name or "", u.last_name or "") if p]
    base = ""
    if parts:
        base = " ".join(parts)[:40]
    elif u.username:
        base = f"@{u.username}"[:40]
    else:
        base = str(u.id)
    if getattr(u, "is_bot", False):
        return f"{base} (бот)" if "(бот)" not in base else base
    return base


def _payload_from_message(message: Message) -> str:
    if message.text:
        return message.text.strip()
    if message.caption:
        return message.caption.strip()
    if message.photo:
        return "[фото]"
    if message.document:
        return "[файл]"
    if message.video:
        return "[видео]"
    if message.voice:
        return "[голос]"
    return "[медиа]"


def enabled() -> bool:
    return _truthy("GROUP_TRANSCRIPT_ENABLED", True)


def max_entries() -> int:
    try:
        return max(20, min(int(os.getenv("GROUP_TRANSCRIPT_MAX_ENTRIES", "120")), 2000))
    except ValueError:
        return 120


def prompt_lines() -> int:
    try:
        return max(5, min(int(os.getenv("GROUP_TRANSCRIPT_PROMPT_LINES", "20")), 60))
    except ValueError:
        return 20


def max_commitments() -> int:
    try:
        return max(3, min(int(os.getenv("GROUP_TRANSCRIPT_MAX_COMMITMENTS", "12")), 40))
    except ValueError:
        return 12


def line_max_chars() -> int:
    try:
        return max(40, min(int(os.getenv("GROUP_TRANSCRIPT_LINE_MAX_CHARS", "160")), 400))
    except ValueError:
        return 160


def _merge_roster(data: Dict[str, Any], entry: Dict[str, Any]) -> None:
    role = entry.get("role")
    uid = str(entry.get("uid") or "").strip()
    if role == "assistant" or not uid:
        return
    roster: Dict[str, Any] = data.get("roster") or {}
    if not isinstance(roster, dict):
        roster = {}
    roster[uid] = {
        "who": str(entry.get("who") or "?")[:48],
        "last_t": int(entry.get("t") or 0),
        "is_bot": bool(entry.get("is_bot")),
    }
    cap = max_roster_ids()
    if len(roster) > cap:
        pairs = sorted(roster.items(), key=lambda kv: int((kv[1] or {}).get("last_t") or 0), reverse=True)
        roster = dict(pairs[:cap])
    data["roster"] = roster


def _append_entry(group_id: str, entry: Dict[str, Any]) -> None:
    data = _load(group_id)
    entries: List[Dict[str, Any]] = data.get("entries") or []
    if not isinstance(entries, list):
        entries = []
    entries.append(entry)
    cap = max_entries()
    data["entries"] = entries[-cap:]
    _merge_roster(data, entry)
    _save(group_id, data)


def _maybe_add_commitment(group_id: str, user_id: str, text: str) -> None:
    if not _truthy("GROUP_TRANSCRIPT_COMMITMENTS", True):
        return
    if not text or not _COMMITMENT_RE.search(text):
        return
    data = _load(group_id)
    com: List[Dict[str, Any]] = data.get("commitments") or []
    if not isinstance(com, list):
        com = []
    _t = int(time.time())
    row = {
        "t": _t,
        "iso": _utc_iso(_t),
        "uid": str(user_id),
        "text": _clip(text, 400),
    }
    com.append(row)
    data["commitments"] = com[-max_commitments() :]
    _save(group_id, data)


def record_skipped_group_message(message: Message) -> None:
    """Сообщение в группе без триггера бота — только в буфер."""
    if not enabled():
        return
    if not message.chat or message.chat.id is None:
        return
    gid = str(message.chat.id)
    text = _payload_from_message(message)
    _is_bot = bool(message.from_user and getattr(message.from_user, "is_bot", False))
    _t = int(time.time())
    entry = {
        "t": _t,
        "iso": _utc_iso(_t),
        "uid": str(message.from_user.id) if message.from_user else "",
        "who": _display_name(message),
        "role": "user",
        "text": _clip(text, 2000),
        "triggered_bot": False,
        "is_bot": _is_bot,
    }
    try:
        _append_entry(gid, entry)
    except Exception as e:
        logger.debug("group_transcript skip append: %s", e)


def record_triggered_user_turn(message: Message, resolved_text: str) -> None:
    """Сообщение, после которого бот отвечает (уже с текстом STT и т.д.)."""
    if not enabled():
        return
    if not message.chat or message.chat.id is None:
        return
    gid = str(message.chat.id)
    text = (resolved_text or "").strip() or _payload_from_message(message)
    uid = str(message.from_user.id) if message.from_user else ""
    _is_bot = bool(message.from_user and getattr(message.from_user, "is_bot", False))
    _t = int(time.time())
    entry = {
        "t": _t,
        "iso": _utc_iso(_t),
        "uid": uid,
        "who": _display_name(message),
        "role": "user",
        "text": _clip(text, 2000),
        "triggered_bot": True,
        "is_bot": _is_bot,
    }
    try:
        _append_entry(gid, entry)
        _maybe_add_commitment(gid, uid, text)
    except Exception as e:
        logger.debug("group_transcript user append: %s", e)


def record_assistant_reply(group_id: str, text: str) -> None:
    if not enabled() or not group_id:
        return
    t = (text or "").strip()
    if not t:
        return
    _ts = int(time.time())
    entry = {
        "t": _ts,
        "iso": _utc_iso(_ts),
        "uid": "",
        "who": "бот",
        "role": "assistant",
        "text": _clip(t, 2000),
        "triggered_bot": False,
        "is_bot": True,
    }
    try:
        _append_entry(str(group_id), entry)
    except Exception as e:
        logger.debug("group_transcript assistant append: %s", e)


def _format_compact(entries: List[Dict[str, Any]], n_lines: int) -> str:
    if not entries:
        return ""
    tail = entries[-n_lines:]
    lm = line_max_chars()
    lines: List[str] = []
    for e in tail:
        if not isinstance(e, dict):
            continue
        who = str(e.get("who") or "?")
        role = e.get("role")
        if e.get("is_bot") and "(бот)" not in who and role != "assistant":
            who = f"{who} (бот)"
        prefix = "→" if role == "assistant" else "•"
        body = _clip(str(e.get("text") or ""), lm)
        if not body:
            continue
        lines.append(f"{prefix} {who}: {body}")
    return "\n".join(lines)


def _format_commitments(com: List[Dict[str, Any]]) -> str:
    if not com:
        return ""
    lines: List[str] = []
    for row in com[-max_commitments() :]:
        if not isinstance(row, dict):
            continue
        uid = row.get("uid", "")
        txt = _clip(str(row.get("text") or ""), 220)
        if txt:
            lines.append(f"- [user {uid}] {txt}")
    return "\n".join(lines)


def _format_roster_hint(roster: Dict[str, Any], limit: int) -> str:
    if not roster:
        return ""
    rows: List[tuple] = []
    for uid, row in roster.items():
        if not isinstance(row, dict):
            continue
        who = str(row.get("who") or "?").strip()
        if not who:
            continue
        lt = int(row.get("last_t") or 0)
        bot = bool(row.get("is_bot"))
        label = f"{who}" + (" [бот]" if bot else "")
        rows.append((lt, label))
    rows.sort(key=lambda x: -x[0])
    labels = [x[1] for x in rows[:limit]]
    return ", ".join(labels)


def roster_prompt_names() -> int:
    try:
        return max(8, min(int(os.getenv("GROUP_ROSTER_PROMPT_NAMES", "24")), 80))
    except ValueError:
        return 24


def get_brain_extras(group_id: Optional[str]) -> Dict[str, str]:
    """Короткие строки для промпта (пустые, если не группа или выключено)."""
    if not group_id or not enabled():
        return {"transcript_compact": "", "commitments_hint": "", "roster_hint": ""}
    data = _load(str(group_id))
    entries = data.get("entries") or []
    if not isinstance(entries, list):
        entries = []
    compact = _format_compact(entries, prompt_lines())
    ch = _format_commitments(data.get("commitments") or [])
    rdict = data.get("roster") or {}
    if not isinstance(rdict, dict):
        rdict = {}
    rh = _format_roster_hint(rdict, roster_prompt_names())
    return {
        "transcript_compact": compact,
        "commitments_hint": ch,
        "roster_hint": rh,
    }
