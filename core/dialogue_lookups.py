"""
Детерминированный поиск пользовательских реплик по времени (telegram_ts) для подсказки LLM.

recent_messages / dialogue_summary уже содержат ts у user-сообщений (behavior_store);
модель не должна выдумывать текст, если факты ниже пусты.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore

from core.timezone_inference import infer_timezone_from_facts

_CLOCK_RE = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)")

# Спрос про «какое сообщение» + время в том же тексте
_MSG_BY_TIME_HINTS = (
    "сообщен",
    "прислал",
    "писал",
    "написал",
    "отправил",
    "текст",
    "что я",
    "какое ",
    "какой ",
    "напомни",
    "было в",
)


def user_asks_past_message_with_clock(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t or not _CLOCK_RE.search(t):
        return False
    return any(h in t for h in _MSG_BY_TIME_HINTS)


def _parse_first_clock(text: str) -> Optional[Tuple[int, int]]:
    m = _CLOCK_RE.search(text or "")
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return None
    return h, mi


def _ts_to_local_hm(ts: int, tz_name: Optional[str]) -> Optional[Tuple[int, int]]:
    try:
        utc = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        if tz_name and ZoneInfo:
            loc = utc.astimezone(ZoneInfo(tz_name))
        else:
            loc = utc
        return loc.hour, loc.minute
    except (OSError, ValueError, TypeError, OverflowError):
        return None


def _scan_summary_for_user_ts(summary: str) -> List[Tuple[int, str]]:
    """Фрагменты вида «user ts=unix:text» из dialogue_summary (overflow от behavior_store)."""
    out: List[Tuple[int, str]] = []
    s = summary or ""
    if not s.strip():
        return out
    for part in re.split(r"\s+│\s+", s):
        part = part.strip()
        m = re.match(r"^user ts=(\d+):(.+)$", part, re.DOTALL)
        if not m:
            continue
        try:
            ts = int(m.group(1))
        except ValueError:
            continue
        txt = (m.group(2) or "").strip()
        if txt:
            out.append((ts, txt[:2000]))
    return out


def _collect_user_rows(recent_messages: List[Any], *, source: str = "recent") -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not isinstance(recent_messages, list):
        return rows
    for m in recent_messages:
        if not isinstance(m, dict):
            continue
        if str(m.get("role") or "").lower() != "user":
            continue
        ts = m.get("telegram_ts")
        if ts is None:
            continue
        try:
            ts_i = int(ts)
        except (TypeError, ValueError):
            continue
        text = str(m.get("text") or "").strip()
        rows.append({"ts": ts_i, "text": text, "source": source})
    return rows


def build_dialogue_lookup_hint_for_llm(
    user_text: str,
    *,
    recent_messages: List[Any],
    dialogue_summary: str,
    user_facts: Dict[str, Any],
    user_id: Optional[str] = None,
    group_id: Optional[str] = None,
    archive_messages: Optional[List[Any]] = None,
) -> str:
    if not user_asks_past_message_with_clock(user_text):
        return ""
    clock = _parse_first_clock(user_text)
    if not clock:
        return ""
    want_h, want_m = clock
    facts = user_facts if isinstance(user_facts, dict) else {}
    tz = infer_timezone_from_facts(facts)

    arch_list: List[Any] = []
    if archive_messages is not None:
        arch_list = archive_messages
    elif user_id:
        try:
            from core.message_archive import load_message_archive_items

            arch_list = load_message_archive_items(str(user_id), group_id)
        except Exception:
            arch_list = []

    matches: List[str] = []
    seen_ts: set[int] = set()

    for row in _collect_user_rows(recent_messages):
        hm = _ts_to_local_hm(row["ts"], tz)
        if hm != (want_h, want_m):
            continue
        ts = row["ts"]
        if ts in seen_ts:
            continue
        seen_ts.add(ts)
        utc_s = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        loc_note = f"локально ({tz}) {want_h:02d}:{want_m:02d}" if tz else f"по UTC {want_h:02d}:{want_m:02d}"
        snippet = (row["text"] or "").replace("\n", " ").strip()[:900]
        matches.append(f"- ts_unix={ts} {loc_note}; telegram_utc={utc_s}; текст: {snippet}")

    for row in _collect_user_rows(arch_list, source="archive"):
        hm = _ts_to_local_hm(row["ts"], tz)
        if hm != (want_h, want_m):
            continue
        ts = row["ts"]
        if ts in seen_ts:
            continue
        seen_ts.add(ts)
        utc_s = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        loc_note = f"локально ({tz}) {want_h:02d}:{want_m:02d}" if tz else f"по UTC {want_h:02d}:{want_m:02d}"
        snippet = (row["text"] or "").replace("\n", " ").strip()[:900]
        matches.append(
            f"- ts_unix={ts} {loc_note}; telegram_utc={utc_s}; текст (архив): {snippet}"
        )

    for ts, txt in _scan_summary_for_user_ts(dialogue_summary):
        hm = _ts_to_local_hm(ts, tz)
        if hm != (want_h, want_m):
            continue
        if ts in seen_ts:
            continue
        seen_ts.add(ts)
        utc_s = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        loc_note = f"локально ({tz})" if tz else "UTC"
        snippet = txt.replace("\n", " ").strip()[:900]
        matches.append(
            f"- ts_unix={ts} ({loc_note}, совпадение {want_h:02d}:{want_m:02d}); telegram_utc={utc_s}; "
            f"текст (фрагмент из сводки): {snippet}"
        )

    tz_line = (
        f"Часовой пояс для сопоставления: {tz}."
        if tz
        else "Часовой пояс пользователя неизвестен — сопоставление по UTC (если пользователь имел в виду локальное время, ответ может не совпасть; попроси уточнить пояс)."
    )

    if not matches:
        return (
            "Запрос: найти пользовательское сообщение по времени на часах.\n"
            f"{tz_line}\n"
            f"Искомое локальное/UTC время в запросе: {want_h:02d}:{want_m:02d}.\n"
            "В последних репликах, в отдельном архиве (DIALOGUE_MESSAGE_ARCHIVE_MAX) и в сводке диалога "
            "нет user-сообщения с этим временем отправки. Не выдумывай текст — скажи, что в доступной "
            "памяти бота такого сообщения нет; предложи ответ реплаем на сообщение в Telegram или увеличить "
            "DIALOGUE_MESSAGE_ARCHIVE_MAX / DIALOGUE_MEMORY_MAX."
        )

    return (
        "Факты: пользовательские сообщения с временем отправки, совпадающим с запрошенными час:минуты "
        f"({want_h:02d}:{want_m:02d}) при поясе как указано ниже.\n"
        f"{tz_line}\n"
        "Используй только эти строки как цитату; не придумывай другой текст.\n"
        + "\n".join(matches)
    )


def build_relative_time_archive_hint_for_llm(
    user_text: str,
    *,
    user_id: Optional[str],
    group_id: Optional[str],
    user_facts: Dict[str, Any],
    recent_messages: Optional[List[Any]] = None,
    telegram_message_unix: Optional[int] = None,
) -> str:
    """
    «Вчера утром», «неделю назад» + маркеры диалога: вытаскивает реплики из архива и recent_dialogue по telegram_ts.
    """
    from core.relative_dialogue_time import (
        filter_archive_items_by_unix_window,
        format_relative_window_hint_lines,
        merge_archive_and_recent_ts,
        parse_recall_time_window_unix,
        recall_query_wants_earliest,
        user_asks_relative_dialogue_time,
    )

    if not user_asks_relative_dialogue_time(user_text):
        return ""
    if not user_id:
        return ""
    facts = user_facts if isinstance(user_facts, dict) else {}
    try:
        ref_ts = int(telegram_message_unix) if telegram_message_unix is not None else None
    except (TypeError, ValueError):
        ref_ts = None
    if ref_ts is not None:
        ref = datetime.fromtimestamp(ref_ts, tz=timezone.utc)
    else:
        ref = datetime.now(timezone.utc)
    parsed = parse_recall_time_window_unix(user_text, user_facts=facts, reference_utc=ref)
    if not parsed:
        return ""
    start_u, end_u, label = parsed
    try:
        from core.message_archive import load_message_archive_items

        arch = load_message_archive_items(str(user_id), group_id)
    except Exception:
        arch = []
    merged = merge_archive_and_recent_ts(arch, recent_messages)
    newest_first = not recall_query_wants_earliest(user_text)
    rows = filter_archive_items_by_unix_window(
        merged, start_u, end_u, newest_first=newest_first
    )
    return format_relative_window_hint_lines(
        rows, label=label, picked_earliest=not newest_first
    )
