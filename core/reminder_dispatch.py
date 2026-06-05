"""Напоминания: хранение (JSON) + фоновая доставка в Telegram."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_reminder_bot: Any = None
_pending_soon_wakes: Set[Tuple[int, int]] = set()
_reminder_lock = threading.Lock()

_TIME_RE = re.compile(
    r"(?i)(?:в\s+)?(\d{1,2})[:.](\d{2})(?:\s*(?P<tz>utc|мск|msk))?"
)
_REL_MIN_RE = re.compile(r"(?i)через\s+(\d+)\s*(?:мин|минут|минуты|minute|minutes|m)\b")
_REL_HOUR_RE = re.compile(r"(?i)через\s+(\d+)\s*(?:час|часа|часов|hour|hours|h)\b")
_DAYPART_RE = re.compile(r"(?i)\b(утром|днём|днем|вечером|ночью)\b")
_SENT_FLAG = "_telegram_sent"

_REMINDER_BAD_UID_WARNED: Set[str] = set()
_REMINDER_CHAT_NOT_FOUND_WARNED: Set[str] = set()


def _reminder_telegram_chat_id(uid: Any) -> Optional[int]:
    """Chat id для send_message: только целое (строка из цифр допустима). Ключи вроде u_test_nl — нет."""
    if uid is None:
        return None
    if isinstance(uid, bool):
        return None
    if isinstance(uid, int):
        return uid if uid != 0 else None
    s = str(uid).strip()
    if not s:
        return None
    if s[0] in "+-":
        body = s[1:].lstrip("+")
        if not body.isdigit():
            return None
    elif not s.isdigit():
        return None
    try:
        n = int(s)
    except (ValueError, OverflowError):
        return None
    return n if n != 0 else None


def _reminder_daypart_hour(part_word: str) -> int:
    """Час локального дня для «вечером»/«утром» (настраивается через REMINDER_DAYPART_*_HOUR)."""
    w = (part_word or "").strip().lower()
    defaults = {
        "утром": (9, "REMINDER_DAYPART_MORNING_HOUR"),
        "днём": (14, "REMINDER_DAYPART_AFTERNOON_HOUR"),
        "днем": (14, "REMINDER_DAYPART_AFTERNOON_HOUR"),
        "вечером": (20, "REMINDER_DAYPART_EVENING_HOUR"),
        "ночью": (22, "REMINDER_DAYPART_NIGHT_HOUR"),
    }
    if w not in defaults:
        return 20
    default_h, env_key = defaults[w]
    try:
        h = int((os.getenv(env_key) or str(default_h)).strip())
    except ValueError:
        h = default_h
    return max(0, min(23, h))


def _reminders_path() -> Path:
    root = (os.getenv("GEMMA_PROJECT_ROOT") or ".").strip() or "."
    p = Path(root) / "data" / "runtime" / "light_reminders.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_reminders() -> Dict[str, Any]:
    """Публичный доступ к хранилищу напоминаний."""
    return _load()


def save_reminders(data: Dict[str, Any]) -> None:
    """Публичная запись хранилища напоминаний."""
    _save(data)


def _load() -> Dict[str, Any]:
    path = _reminders_path()
    if not path.is_file():
        return {"users": {}}
    try:
        with _reminder_lock:
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"users": {}}


def _save(data: Dict[str, Any]) -> None:
    path = _reminders_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with _reminder_lock:
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)


def _reminder_default_tz_name() -> str:
    return (os.getenv("REMINDER_DEFAULT_TIMEZONE") or "Europe/Moscow").strip() or "Europe/Moscow"


def register_reminder_bot(bot: Any) -> None:
    """Сохранить bot для доставки soon-after-schedule и boot-tick."""
    global _reminder_bot
    _reminder_bot = bot
    reschedule_pending_soon_wakes()


def reschedule_pending_soon_wakes() -> None:
    """После старта бота — таймеры на ближайшие due и сразу просроченные."""
    bot = _reminder_bot
    if bot is None:
        return
    now = int(time.time())
    try:
        max_sec = int(float(os.getenv("REMINDER_SOON_WAKE_MAX_SEC", "7200")))
    except ValueError:
        max_sec = 7200
    data = _load()
    seen_due: Set[int] = set()
    for items in (data.get("users") or {}).values():
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            due = int(it.get("due_ts") or 0)
            if due <= now or due - now > max_sec:
                continue
            if due in seen_due:
                continue
            seen_due.add(due)
            _maybe_schedule_soon_wake(due)
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(tick_due_reminders(bot))
    except RuntimeError:
        pass


def _user_tz(user_id: str) -> str:
    uid = str(user_id or "").strip()
    try:
        from core.behavior_store import BehaviorStore
        from core.timezone_inference import infer_timezone_from_facts

        rec = BehaviorStore().load(uid, None) if uid else {}
        uf = rec.get("user_facts") if isinstance(rec.get("user_facts"), dict) else {}
        tz = infer_timezone_from_facts(uf)
        if tz:
            return tz
    except Exception as e:
        logger.debug('%s optional failed: %s', 'reminder_dispatch', e, exc_info=True)
    return _reminder_default_tz_name()


def _format_due_log(due_ts: int, tz_name: str) -> str:
    try:
        from zoneinfo import ZoneInfo

        dt = datetime.fromtimestamp(due_ts, tz=ZoneInfo(tz_name))
        return f"{dt.strftime('%Y-%m-%d %H:%M')} {tz_name}"
    except Exception:
        return str(due_ts)


def _maybe_schedule_soon_wake(due_ts: int) -> None:
    bot = _reminder_bot
    if bot is None:
        return
    now = int(time.time())
    delay = max(0, due_ts - now)
    try:
        max_sec = int(float(os.getenv("REMINDER_SOON_WAKE_MAX_SEC", "7200")))
    except ValueError:
        max_sec = 7200
    if delay > max_sec:
        return
    wake_key = (due_ts, id(bot))
    if wake_key in _pending_soon_wakes:
        return
    _pending_soon_wakes.add(wake_key)

    async def _job() -> None:
        try:
            if delay > 0:
                await asyncio.sleep(delay + 0.5)
            await tick_due_reminders(bot)
        finally:
            _pending_soon_wakes.discard(wake_key)

    try:
        asyncio.get_running_loop().create_task(_job())
    except RuntimeError:
        _pending_soon_wakes.discard(wake_key)


def parse_due_ts(event: Any, *, user_id: str = "") -> Optional[int]:
    """Из event (str/dict) или текста — Unix UTC когда напомнить."""
    tz_name = _user_tz(user_id) if user_id else _reminder_default_tz_name()
    try:
        from zoneinfo import ZoneInfo

        z = ZoneInfo(tz_name)
    except Exception:
        z = None

    now = datetime.now(z) if z else datetime.utcnow()
    text = ""
    if isinstance(event, dict):
        text = str(event.get("text") or event.get("title") or event.get("event") or "")
        t_raw = event.get("time") or event.get("at") or event.get("due")
        if t_raw:
            text = f"{text} {t_raw}"
    elif isinstance(event, str):
        text = event

    low = text.lower()
    day_offset = 0
    if "послезавтра" in low:
        day_offset = 2
    elif "завтра" in low:
        day_offset = 1

    m_rel = _REL_MIN_RE.search(text)
    if m_rel:
        return int(time.time()) + int(m_rel.group(1)) * 60

    m_rel_h = _REL_HOUR_RE.search(text)
    if m_rel_h:
        return int(time.time()) + int(m_rel_h.group(1)) * 3600

    m_part = _DAYPART_RE.search(text)
    if m_part:
        h = _reminder_daypart_hour(m_part.group(1))
        now_local = datetime.now(z) if z else datetime.utcnow()
        base = now_local + timedelta(days=day_offset)
        due = base.replace(hour=h, minute=0, second=0, microsecond=0)
        if day_offset == 0 and due <= now_local:
            due += timedelta(days=1)
        if z:
            return int(due.timestamp())
        return int(due.replace(tzinfo=None).timestamp())

    m = _TIME_RE.search(text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mi <= 59:
            tz_suffix = (m.group("tz") or "").strip().lower()
            z_parse = z
            if tz_suffix == "utc":
                try:
                    from zoneinfo import ZoneInfo

                    z_parse = ZoneInfo("UTC")
                except Exception:
                    z_parse = None
            elif tz_suffix in {"мск", "msk"}:
                try:
                    from zoneinfo import ZoneInfo

                    z_parse = ZoneInfo("Europe/Moscow")
                except Exception:
                    z_parse = z
            now_local = datetime.now(z_parse) if z_parse else datetime.utcnow()
            base = now_local + timedelta(days=day_offset)
            due = base.replace(hour=h, minute=mi, second=0, microsecond=0)
            if day_offset == 0 and due <= now_local:
                due += timedelta(days=1)
            if z_parse:
                return int(due.timestamp())
            return int(due.replace(tzinfo=None).timestamp())

    return None


def _compute_next_recurring_ts(
    *,
    dows: Set[int],
    hour: int,
    minute: int,
    user_id: str,
    after_ts: Optional[int] = None,
) -> int:
    """Следующий Unix UTC для weekly recurring (dow: 0=пн … 6=вс)."""
    try:
        from zoneinfo import ZoneInfo

        z = ZoneInfo(_user_tz(user_id))
    except Exception:
        z = None
    base_ts = int(after_ts or time.time()) + 30
    if z:
        start = datetime.fromtimestamp(base_ts, tz=z)
    else:
        start = datetime.utcfromtimestamp(base_ts)
    for day_ahead in range(0, 14):
        cand = start + timedelta(days=day_ahead)
        if cand.weekday() in dows:
            due = cand.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if due > start:
                if z:
                    return int(due.timestamp())
                return int(due.replace(tzinfo=None).timestamp())
    return base_ts + 86400


def add_recurring_reminder(
    user_id: str,
    text: str,
    *,
    dows: Set[int],
    hour: int,
    minute: int,
) -> str:
    """Еженедельное напоминание (повтор после доставки)."""
    uid = str(user_id or "unknown")
    body = (text or "").strip() or "напоминание"
    valid = {int(d) for d in dows if 0 <= int(d) <= 6}
    if not valid:
        valid = {0}
    due_ts = _compute_next_recurring_ts(
        dows=valid, hour=int(hour), minute=int(minute), user_id=uid
    )
    rid = uuid.uuid4().hex[:10]
    data = _load()
    data.setdefault("users", {})
    data["users"].setdefault(uid, [])
    data["users"][uid].append(
        {
            "id": rid,
            "text": body,
            "due_ts": due_ts,
            "created": int(time.time()),
            "recurring": {"dow": sorted(valid), "hour": int(hour), "minute": int(minute)},
        }
    )
    _save(data)
    tz_name = _user_tz(uid)
    logger.info(
        "[reminder] recurring scheduled uid=%s due_ts=%s local=%s id=%s dows=%s",
        uid,
        due_ts,
        _format_due_log(due_ts, tz_name),
        rid,
        sorted(valid),
    )
    _maybe_schedule_soon_wake(due_ts)
    return rid


def add_reminder(user_id: str, text: str, due_ts: int) -> str:
    """Добавить напоминание. Возвращает id."""
    uid = str(user_id or "unknown")
    body = (text or "").strip() or "напоминание"
    due_ts = int(due_ts)
    rid = uuid.uuid4().hex[:10]
    data = _load()
    data.setdefault("users", {})
    data["users"].setdefault(uid, [])
    data["users"][uid].append(
        {"id": rid, "text": body, "due_ts": due_ts, "created": int(time.time())}
    )
    _save(data)
    tz_name = _user_tz(uid)
    logger.info(
        "[reminder] scheduled uid=%s due_ts=%s local=%s id=%s",
        uid,
        due_ts,
        _format_due_log(due_ts, tz_name),
        rid,
    )
    _maybe_schedule_soon_wake(due_ts)
    return rid


_CANCEL_HINT_STOPWORDS = frozenset(
    {
        "про",
        "об",
        "о",
        "the",
        "about",
        "на",
        "по",
        "это",
        "то",
        "мне",
        "мое",
        "моё",
        "мою",
        "мой",
    }
)


def _cancel_hint_matches_body(hint: str, body: str) -> bool:
    """«про тест» должен находить «сказать «тест пройден»»."""
    h = (hint or "").strip().lower()
    b = (body or "").strip().lower()
    if not h or not b:
        return False
    if h in b:
        return True
    tokens = [
        w
        for w in re.findall(r"[а-яёa-z0-9]{2,}", h)
        if w not in _CANCEL_HINT_STOPWORDS and len(w) >= 3
    ]
    if not tokens:
        tokens = [w for w in re.findall(r"[а-яёa-z0-9]{2,}", h) if w not in _CANCEL_HINT_STOPWORDS]
    return bool(tokens) and any(t in b for t in tokens)


def _active_reminders_for_user(items: List[Any]) -> List[Dict[str, Any]]:
    """Непрошедшие разовые и все повторяющиеся напоминания."""
    now = int(time.time())
    out: List[Dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        text = str(it.get("text") or "").strip()
        if not text:
            continue
        if it.get("recurring"):
            out.append(it)
            continue
        due = int(it.get("due_ts") or 0)
        if due > now and not it.get(_SENT_FLAG):
            out.append(it)
    return out


def list_active_reminders_sorted(user_id: str) -> List[Dict[str, Any]]:
    """Активные напоминания в порядке /rlist (по due_ts, 1-based индекс в UI)."""
    uid = str(user_id or "").strip()
    if not uid:
        return []
    data = _load()
    items = (data.get("users") or {}).get(uid)
    if not isinstance(items, list) or not items:
        return []
    return sorted(_active_reminders_for_user(items), key=lambda x: int(x.get("due_ts") or 0))


def cancel_reminder_by_list_index(user_id: str, index: int) -> Tuple[int, List[str]]:
    """Отмена по номеру строки из /rlist (1-based)."""
    if index < 1:
        return 0, []
    active = list_active_reminders_sorted(user_id)
    if index > len(active):
        return 0, []
    rid = str(active[index - 1].get("id") or "").strip()
    if not rid:
        return 0, []
    return cancel_user_reminders(user_id, reminder_id=rid)


def cancel_user_reminders(
    user_id: str,
    *,
    reminder_id: Optional[str] = None,
    text_hint: Optional[str] = None,
    latest_only: bool = False,
    cancel_all: bool = False,
) -> Tuple[int, List[str]]:
    """
    Удалить напоминания пользователя.
    Возвращает (число удалённых, подписи удалённых).
    """
    uid = str(user_id or "").strip()
    if not uid:
        return 0, []
    data = _load()
    users = data.get("users") or {}
    items = users.get(uid)
    if not isinstance(items, list) or not items:
        return 0, []

    active = _active_reminders_for_user(items)
    if not active:
        return 0, []

    remove_ids: Set[str] = set()
    if reminder_id:
        remove_ids.add(str(reminder_id).strip())
    elif cancel_all:
        remove_ids = {str(it.get("id") or "") for it in active if it.get("id")}
    elif text_hint:
        hint = (text_hint or "").strip()
        if hint:
            for it in active:
                body = str(it.get("text") or "")
                if _cancel_hint_matches_body(hint, body):
                    rid = str(it.get("id") or "")
                    if rid:
                        remove_ids.add(rid)
        if not remove_ids and len(active) == 1:
            rid = str(active[0].get("id") or "")
            if rid:
                remove_ids.add(rid)
    if latest_only and not remove_ids:
        # Последнее в файле (чаще всего недавно добавленное recurring).
        pick = active[-1]
        rid = str(pick.get("id") or "")
        if rid:
            remove_ids.add(rid)
    if not remove_ids and not cancel_all and not text_hint and not latest_only:
        # «Отмени напоминание» без уточнения — снять все активные.
        remove_ids = {str(it.get("id") or "") for it in active if it.get("id")}

    if not remove_ids:
        return 0, []

    removed_labels: List[str] = []
    kept: List[Dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        rid = str(it.get("id") or "")
        if rid and rid in remove_ids:
            removed_labels.append(str(it.get("text") or "напоминание").strip() or "напоминание")
            logger.info("[reminder] cancelled uid=%s id=%s", uid, rid)
            continue
        kept.append(it)
    if not removed_labels:
        return 0, []
    users[uid] = kept
    data["users"] = users
    _save(data)
    return len(removed_labels), removed_labels


def persist_reminder_from_schedule_event(user_id: str, event: Any) -> Optional[str]:
    """Мост Schedule.add_event → light_reminders.json."""
    due = parse_due_ts(event, user_id=user_id)
    if not due:
        return None
    if isinstance(event, dict):
        label = str(event.get("text") or event.get("title") or event.get("event") or "напоминание")
    else:
        label = str(event)
    # убрать время из подписи
    label = _TIME_RE.sub("", label).strip() or "напоминание"
    return add_reminder(user_id, label, due)


async def boot_tick_reminders(bot: Any) -> int:
    """Сразу после старта polling — не пропустить напоминания из окна рестарта."""
    if bot is None:
        return 0
    n = await tick_due_reminders(bot)
    if n:
        logger.info("[reminder] boot delivered=%s", n)
    return n


def _stale_cutoff_sec() -> int:
    try:
        days = int(float(os.getenv("REMINDER_STALE_DAYS", "14")))
    except ValueError:
        days = 14
    return max(1, days) * 86400


def _prune_invalid_user_keys(data: Dict[str, Any]) -> bool:
    """Убрать ключи users, которые не являются Telegram chat id (u_test_nl, u_by_num из старых тестов)."""
    users = data.get("users") or {}
    if not isinstance(users, dict):
        data["users"] = {}
        return True
    changed = False
    for uid in list(users.keys()):
        if _reminder_telegram_chat_id(uid) is not None:
            continue
        del users[uid]
        changed = True
        logger.info("[reminder] removed bogus uid key %r from light_reminders.json", uid)
    data["users"] = users
    return changed


def _prune_stale_reminders(data: Dict[str, Any], now: int) -> bool:
    """Удалить напоминания, просроченные дольше REMINDER_STALE_DAYS (не доставленные)."""
    cutoff = now - _stale_cutoff_sec()
    changed = False
    users = data.get("users") or {}
    for uid, items in list(users.items()):
        if not isinstance(items, list):
            continue
        kept: List[Dict[str, Any]] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            due = int(it.get("due_ts") or 0)
            if due < cutoff and not it.get(_SENT_FLAG):
                changed = True
                logger.info("[reminder] pruned stale uid=%s id=%s due_ts=%s", uid, it.get("id"), due)
                continue
            kept.append(it)
        users[uid] = kept
    data["users"] = users
    return changed


async def tick_due_reminders(bot: Any) -> int:
    """Отправить все просроченные напоминания. Возвращает число отправленных."""
    if bot is None:
        return 0
    now = int(time.time())
    data = _load()
    changed = _prune_invalid_user_keys(data)
    if _prune_stale_reminders(data, now):
        changed = True
    if changed:
        _save(data)
    users = data.get("users") or {}
    sent = 0
    from core.telegram_util import sanitize_html

    for uid, items in list(users.items()):
        if not isinstance(items, list):
            continue
        chat_id = _reminder_telegram_chat_id(uid)
        if chat_id is None:
            continue
        keep: List[Dict[str, Any]] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            due = int(it.get("due_ts") or 0)
            text = str(it.get("text") or "").strip()
            if due <= now and text and not it.get(_SENT_FLAG):
                try:
                    rec = it.get("recurring") if isinstance(it.get("recurring"), dict) else None
                    prefix = "🔁 " if rec else "⏰ "
                    msg = sanitize_html(f"{prefix}Напоминание: {text}")
                    await bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
                    sent += 1
                    logger.info("[reminder] delivered uid=%s id=%s recurring=%s", uid, it.get("id"), bool(rec))
                    if rec:
                        dows_raw = rec.get("dow") or []
                        dows_set = {int(d) for d in dows_raw if str(d).isdigit() or isinstance(d, int)}
                        if dows_set:
                            next_ts = _compute_next_recurring_ts(
                                dows=dows_set,
                                hour=int(rec.get("hour") or 9),
                                minute=int(rec.get("minute") or 0),
                                user_id=uid,
                                after_ts=now,
                            )
                            it = dict(it)
                            it["due_ts"] = next_ts
                            it.pop(_SENT_FLAG, None)
                            keep.append(it)
                            _maybe_schedule_soon_wake(next_ts)
                        continue
                except Exception as e:
                    err_s = str(e).lower()
                    if "chat not found" in err_s:
                        wkey = f"{uid}"
                        if wkey not in _REMINDER_CHAT_NOT_FOUND_WARNED:
                            _REMINDER_CHAT_NOT_FOUND_WARNED.add(wkey)
                            logger.warning(
                                "[reminder] dropped uid=%s reminders: chat not found (%s)",
                                uid,
                                e,
                            )
                        users.pop(uid, None)
                        break
                    else:
                        logger.warning("[reminder] send failed uid=%s: %s", uid, e)
                    keep.append(it)
                    continue
            else:
                keep.append(it)
        users[uid] = keep
    data["users"] = users
    _save(data)
    return sent
