"""
Естественный язык → напоминание в light_reminders.json (без /radd и без LLM).
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

from core.reminder_dispatch import (
    add_reminder,
    cancel_reminder_by_list_index,
    cancel_user_reminders,
    list_active_reminders_sorted,
    parse_due_ts,
)

logger = logging.getLogger(__name__)

# Только императив «напомни…» / «поставь напоминание» — не существительное
# «напоминание» в длинном тексте («конкретное напоминание» в статье).
_REMINDER_CUE_RE = re.compile(
    r"(?i)(?:"
    r"\bнапомни\w*|\bremind\b|"
    r"\bне\s+забудь|\bне\s+забыть|"
    r"\bпоставь\s+напоминани\w*|\bсделай\s+напоминани\w*|\bсоздай\s+напоминани\w*"
    r")"
)

# Длинная переписка/репост — не запрос завести напоминание.
_REMINDER_PROSE_SKIP_RE = re.compile(
    r"(?i)(?:"
    r"https?://|t\.me/|channel\s+\d|"
    r"\bжюри\b|\bприсяжн\w*|\bиск\b|\bopenai\b|"
    r"\bии-агент|\bагентов\b.*\bдн|\bэксперимент\b"
    r")"
)

_STRIP_FILLER_RE = re.compile(
    r"(?i)\b(?:"
    r"сегодня|завтра|послезавтра|"
    r"через\s+\d+\s*(?:мин|минут|минуты|час|часа|часов|hour|hours)|"
    r"мне|себе|пожалуйста|чтобы|что|надо|нужно"
    r")\b"
)

_TIME_FRAGMENT_RE = re.compile(
    r"(?i)(?:в\s+)?\d{1,2}[:.]\d{2}(?:\s*(?:utc|мск|msk))?"
)

_CANCEL_REMINDER_RE = re.compile(
    r"(?i)(?:"
    r"\bотмен\w*\s+(?:это\s+)?напоминани\w*|\bотмен\w*\s+напоминани\w*|"
    r"\bубери\s+напоминани\w*|\bудали\s+напоминани\w*|"
    r"\bвыключи\s+напоминани\w*|\bне\s+напоминай\b|"
    r"\bбольше\s+не\s+напоминай|\bcancel\s+(?:the\s+)?reminder"
    r")"
)


def nl_reminder_enabled() -> bool:
    raw = os.getenv("REMINDER_NL_ENABLED", "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _looks_like_reminder_setup_intent(text: str) -> bool:
    t = (text or "").strip()
    if not t or t.startswith("/"):
        return False
    if not _REMINDER_CUE_RE.search(t):
        return False
    if len(t) > 420 and t.count("\n") >= 3:
        return False
    if len(t) > 280 and _REMINDER_PROSE_SKIP_RE.search(t):
        return False
    return True


def _parse_cancel_list_index(text: str) -> Optional[int]:
    """Номер строки из /rlist: «отмени напоминание 2», «удали 3»."""
    t = (text or "").strip()
    if not t:
        return None
    patterns = (
        r"(?i)(?:напоминани\w*|reminder)\s*(?:#|№|номер\s*)?(\d{1,3})\s*\.?$",
        r"(?i)^(?:отмен\w*|удал\w*|убер\w*|сними)\s+(?:напоминани\w*\s+)?#?(\d{1,3})\s*\.?$",
    )
    for pat in patterns:
        m = re.search(pat, t)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 99:
                return n
    return None


def looks_like_cancel_reminder_request(text: str) -> bool:
    t = (text or "").strip()
    if not t or t.startswith("/") or len(t) > 160:
        return False
    if _parse_cancel_list_index(t) is not None:
        return True
    return bool(_CANCEL_REMINDER_RE.search(t))


def _cancel_hint_from_text(text: str) -> Tuple[bool, bool, str]:
    """(cancel_all, latest_only, text_hint) из фразы отмены."""
    low = (text or "").strip().lower()
    cancel_all = bool(re.search(r"(?i)\b(?:все|всех|всё)\b", low)) and "напомин" in low
    latest_only = bool(re.search(r"(?i)\bэто\b", low))
    hint = ""
    m = re.search(r"(?i)отмен\w*\s+(?:это\s+)?напоминани\w*\s*(.*)$", (text or "").strip())
    if m:
        hint = (m.group(1) or "").strip()
        if hint.lower() in {"это", "это.", "это!", ""}:
            hint = ""
        elif latest_only:
            hint = ""
    return cancel_all, latest_only, hint


def try_cancel_natural_reminder(user_id: str, text: str) -> Optional[Dict[str, Any]]:
    """Отмена напоминания без LLM (разовые и 🔁 повторяющиеся)."""
    if not nl_reminder_enabled():
        return None
    uid = str(user_id or "").strip()
    raw = (text or "").strip()
    if not uid or not raw or raw.startswith("/"):
        return None
    if not looks_like_cancel_reminder_request(raw):
        return None
    try:
        from core.heuristic_context_gate import should_run_shortcut

        if not should_run_shortcut("reminder_cancel", raw).allowed:
            return None
    except Exception as e:
        logger.debug("reminder_cancel gate: %s", e)

    list_idx = _parse_cancel_list_index(raw)
    if list_idx is not None:
        n, labels = cancel_reminder_by_list_index(uid, list_idx)
        if n <= 0:
            return {
                "ok": False,
                "reply": f"Нет напоминания №{list_idx}. /rlist — актуальный список.",
            }
        return {
            "ok": True,
            "reply": f"Отменил напоминание №{list_idx}: «{labels[0]}».",
            "cancelled": n,
            "labels": labels,
        }

    cancel_all, latest_only, hint = _cancel_hint_from_text(raw)
    active = list_active_reminders_sorted(uid)
    if len(active) > 1 and not cancel_all and not hint and not latest_only:
        lines = [
            f"{i}. {str(it.get('text') or 'напоминание').strip()}"
            for i, it in enumerate(active[:8], start=1)
        ]
        return {
            "ok": False,
            "reply": (
                f"Активных напоминаний: {len(active)}.\n"
                + "\n".join(lines)
                + "\n\nОтменить по номеру из списка: «отмени напоминание 2» или /rdel 2. /rlist — полный список."
            ),
        }
    n, labels = cancel_user_reminders(
        uid,
        text_hint=hint or None,
        latest_only=latest_only or (bool(hint == "" and not cancel_all) and len(active) <= 1),
        cancel_all=cancel_all,
    )
    if n <= 0:
        return {
            "ok": False,
            "reply": "Активных напоминаний нет — отменять нечего.",
        }
    if n == 1:
        reply = f"Отменил напоминание: «{labels[0]}»."
    else:
        lines = "\n".join(f"• {lb}" for lb in labels[:6])
        reply = f"Отменил напоминаний ({n}):\n{lines}"
    logger.info("[reminder_nl] cancelled uid=%s n=%s", uid, n)
    return {"ok": True, "reply": reply, "cancelled": n, "labels": labels}


def looks_like_reminder_request(text: str) -> bool:
    t = (text or "").strip()
    if not _looks_like_reminder_setup_intent(t):
        return False
    return parse_due_ts(t, user_id="") is not None or bool(
        re.search(
            r"(?i)через\s+\d+\s*(?:мин|минут|час|часа|часов)|\b(?:утром|днём|днем|вечером|ночью)\b",
            t,
        )
    )


def extract_reminder_label(text: str) -> str:
    s = (text or "").strip()
    s = _REMINDER_CUE_RE.sub("", s, count=1).strip()
    s = _TIME_FRAGMENT_RE.sub(" ", s)
    s = _STRIP_FILLER_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip(" .,;:-—")
    if len(s) < 2:
        return "напоминание"
    return s[:240]


def _format_due_local(due_ts: int, user_id: str) -> str:
    try:
        from core.reminder_dispatch import _user_tz

        tz_name = _user_tz(user_id)
        z = ZoneInfo(tz_name)
        dt = datetime.fromtimestamp(due_ts, tz=z)
        return dt.strftime("%d.%m %H:%M")
    except Exception:
        return datetime.utcfromtimestamp(due_ts).strftime("%d.%m %H:%M UTC")


def _maybe_persist_user_tz(user_id: str) -> None:
    """Записать timezone в user_facts, если ещё нет — для согласованных напоминаний и «который час»."""
    uid = str(user_id or "").strip()
    if not uid:
        return
    try:
        from core.behavior_store import BehaviorStore
        from core.reminder_dispatch import _user_tz

        store = BehaviorStore()
        rec = store.load(uid, None)
        uf = dict(rec.get("user_facts") or {}) if isinstance(rec.get("user_facts"), dict) else {}
        if str(uf.get("timezone") or "").strip():
            return
        tz = _user_tz(uid)
        if not tz:
            return
        uf["timezone"] = tz
        rec["user_facts"] = uf
        store.save(uid, None, rec)
    except Exception as e:
        logger.debug("reminder_nl persist tz: %s", e)


def try_schedule_natural_reminder(user_id: str, text: str) -> Optional[Dict[str, Any]]:
    """
    Если фраза — запрос напоминания с распознаваемым временем, записать и вернуть ответ пользователю.
    """
    if not nl_reminder_enabled():
        return None
    uid = str(user_id or "").strip()
    raw = (text or "").strip()
    if not uid or not raw:
        return None
    if raw.startswith("/"):
        return None
    if looks_like_cancel_reminder_request(raw):
        return None
    if not _looks_like_reminder_setup_intent(raw):
        return None
    try:
        from core.heuristic_context_gate import should_run_shortcut

        if not should_run_shortcut("reminder_schedule", raw).allowed:
            return None
    except Exception as e:
        logger.debug("reminder_schedule gate: %s", e)

    due_ts = parse_due_ts(raw, user_id=uid)
    if not due_ts:
        if len(raw) > 420 or raw.count("\n") >= 4:
            return None
        return {
            "ok": False,
            "reply": (
                "Не вижу время для напоминания. Напишите, например: "
                "«напомни вечером купить молоко», «напомни сегодня в 22:50 пошла спать» "
                "или «напомни через 30 минут позвонить»."
            ),
        }

    label = extract_reminder_label(raw)
    _maybe_persist_user_tz(uid)
    rid = add_reminder(uid, label, due_ts)
    when = _format_due_local(due_ts, uid)
    low = raw.lower()
    note = ""
    if "сегодня" in low:
        try:
            from core.reminder_dispatch import _user_tz

            z = ZoneInfo(_user_tz(uid))
            now = datetime.now(z)
            due_local = datetime.fromtimestamp(due_ts, tz=z)
            if due_local.date() > now.date():
                note = " (указанное время сегодня уже прошло — напомню завтра в это же время)"
        except Exception as e:
            logger.debug('%s optional failed: %s', 'reminder_nl', e, exc_info=True)
    reply = (
        f"Ок, напомню {when}: «{label}».\n"
        f"Пришлю сообщение в Telegram (обычно в течение нескольких минут после времени).{note}"
    )
    logger.info("[reminder_nl] scheduled uid=%s id=%s due_ts=%s label=%r", uid, rid, due_ts, label)
    return {
        "ok": True,
        "reply": reply,
        "reminder_id": rid,
        "due_ts": due_ts,
        "label": label,
    }
