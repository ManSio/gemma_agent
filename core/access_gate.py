"""
Модерация доступа в личку: заявки, одобрение/отклонение админом (кнопки).

По умолчанию включено; отключить: USER_ACCESS_APPROVAL_REQUIRED=false.
Группы не фильтруются. Админы (ADMIN_USER_IDS / ADMIN_NOTIFY_USER_IDS) всегда проходят.
Состояние: RESILIENCE_RUNTIME_DIR/access_gate_state.json

Гостевая квота (пока заявка не одобрена): USER_ACCESS_GUEST_REPLY_QUOTA (по умолч. 10).
0 — только сообщение «ожидайте», без ответов мозга (старое поведение).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_state: Dict[str, Any] = {"allowed": [], "blocked": [], "pending": [], "guest_replies": {}}
_loaded = False


def _path() -> Path:
    raw = (os.getenv("ACCESS_GATE_STATE_PATH") or "").strip()
    if raw:
        return Path(raw)
    return Path(os.getenv("RESILIENCE_RUNTIME_DIR", "data/runtime")) / "access_gate_state.json"


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def is_approval_required() -> bool:
    return _truthy("USER_ACCESS_APPROVAL_REQUIRED", True)


def _load() -> None:
    global _loaded, _state
    with _lock:
        if _loaded:
            return
        _loaded = True
        p = _path()
        if not p.is_file():
            return
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                gr = raw.get("guest_replies")
                guest_replies: Dict[str, int] = {}
                if isinstance(gr, dict):
                    for k, v in gr.items():
                        try:
                            guest_replies[str(k).strip()] = max(0, int(v))
                        except (TypeError, ValueError):
                            pass
                _state = {
                    "allowed": list(raw.get("allowed") or []),
                    "blocked": list(raw.get("blocked") or []),
                    "pending": list(raw.get("pending") or []) if isinstance(raw.get("pending"), list) else [],
                    "guest_replies": guest_replies,
                }
        except Exception as e:
            logger.warning("access_gate load failed: %s", e)


def _save_locked() -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(_state, ensure_ascii=False, indent=0), encoding="utf-8")
    tmp.replace(p)


def persist() -> None:
    with _lock:
        _save_locked()


def reset_for_tests() -> None:
    """Сброс памяти и повторная загрузка с диска при следующем обращении (тесты)."""
    global _loaded, _state
    with _lock:
        _loaded = False
        _state = {"allowed": [], "blocked": [], "pending": [], "guest_replies": {}}


def _norm_id(uid: str) -> str:
    return str(uid).strip()


def private_pending_message() -> str:
    return (os.getenv("USER_ACCESS_PENDING_MESSAGE") or "⏳ Заявка отправлена администратору. Ожидайте подтверждения.").strip()


def private_blocked_message() -> str:
    return (os.getenv("USER_ACCESS_BLOCKED_MESSAGE") or "⛔ Доступ к боту для этого аккаунта закрыт.").strip()


def private_approved_notice() -> str:
    return (os.getenv("USER_ACCESS_APPROVED_MESSAGE") or "✅ Доступ подтверждён. Можете пользоваться ботом.").strip()


def private_rejected_notice() -> str:
    return (os.getenv("USER_ACCESS_REJECTED_MESSAGE") or "⛔ Заявка отклонена.").strip()


def private_removed_notice() -> str:
    return (os.getenv("USER_ACCESS_REMOVED_MESSAGE") or "Ваш доступ к боту отозван.").strip()


def guest_reply_quota() -> int:
    """Макс. число ответов бота гостю в ЛС, пока заявка в очереди. 0 — без гостевых ответов."""
    try:
        return max(0, min(500, int(os.getenv("USER_ACCESS_GUEST_REPLY_QUOTA", "10"))))
    except ValueError:
        return 10


def guest_quota_exhausted_message() -> str:
    return (
        os.getenv("USER_ACCESS_GUEST_QUOTA_EXHAUSTED_MESSAGE")
        or "⏳ Лимит пробных ответов исчерпан. Дождитесь подтверждения администратора — после одобрения лимит снимется."
    ).strip()


def guest_replies_used(user_id: str) -> int:
    _load()
    uid = _norm_id(user_id)
    with _lock:
        gr = _state.get("guest_replies")
        if not isinstance(gr, dict):
            return 0
        try:
            return max(0, int(gr.get(uid, 0)))
        except (TypeError, ValueError):
            return 0


def increment_guest_replies(user_id: str, delta: int = 1) -> None:
    cap = guest_reply_quota()
    if cap <= 0 or delta <= 0:
        return
    _load()
    uid = _norm_id(user_id)
    with _lock:
        gr = dict(_state.get("guest_replies") or {}) if isinstance(_state.get("guest_replies"), dict) else {}
        cur = 0
        try:
            cur = max(0, int(gr.get(uid, 0)))
        except (TypeError, ValueError):
            cur = 0
        gr[uid] = min(cap, cur + int(delta))
        _state["guest_replies"] = gr
        try:
            _save_locked()
        except Exception as e:
            logger.warning("access_gate guest_replies save: %s", e)


def _clear_guest_replies(uid: str) -> None:
    uid = _norm_id(uid)
    gr = dict(_state.get("guest_replies") or {}) if isinstance(_state.get("guest_replies"), dict) else {}
    if uid in gr:
        del gr[uid]
        _state["guest_replies"] = gr


def evaluate_private_user(user_id: str, is_admin: bool) -> str:
    """Решение для ЛС: allow | blocked | pending (уже в очереди) | enqueue (новый — добавить в pending)."""
    if not is_approval_required() or is_admin:
        return "allow"
    _load()
    uid = _norm_id(user_id)
    with _lock:
        allowed = {_norm_id(x) for x in _state.get("allowed") or []}
        blocked = {_norm_id(x) for x in _state.get("blocked") or []}
        pending_ids = {_norm_id(x.get("user_id")) for x in _state.get("pending") or [] if isinstance(x, dict)}
    if uid in blocked:
        return "blocked"
    if uid in allowed:
        return "allow"
    if uid in pending_ids:
        return "pending"
    return "enqueue"


def enqueue_pending(
    user_id: str,
    *,
    username: Optional[str],
    full_name: str,
) -> bool:
    """Добавить в pending. True если запись новая (нужно уведомить админов)."""
    _load()
    uid = _norm_id(user_id)
    with _lock:
        pending = [x for x in _state.get("pending") or [] if isinstance(x, dict)]
        if any(_norm_id(x.get("user_id")) == uid for x in pending):
            return False
        pending.append(
            {
                "user_id": uid,
                "username": (username or "").strip() or None,
                "full_name": (full_name or "").strip()[:120] or None,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        )
        _state["pending"] = pending
        try:
            _save_locked()
        except Exception as e:
            logger.warning("access_gate save: %s", e)
        return True


def approve(user_id: str) -> Tuple[bool, str]:
    uid = _norm_id(user_id)
    _load()
    with _lock:
        pending = [x for x in _state.get("pending") or [] if isinstance(x, dict) and _norm_id(x.get("user_id")) != uid]
        allowed = list(_state.get("allowed") or [])
        if uid not in allowed:
            allowed.append(uid)
        blocked = [x for x in _state.get("blocked") or [] if _norm_id(x) != uid]
        _state["pending"] = pending
        _state["allowed"] = allowed
        _state["blocked"] = blocked
        _clear_guest_replies(uid)
        try:
            _save_locked()
        except Exception as e:
            return False, str(e)
    return True, ""


def reject(user_id: str) -> Tuple[bool, str]:
    uid = _norm_id(user_id)
    _load()
    with _lock:
        pending = [x for x in _state.get("pending") or [] if isinstance(x, dict) and _norm_id(x.get("user_id")) != uid]
        blocked = list(_state.get("blocked") or [])
        if uid not in blocked:
            blocked.append(uid)
        allowed = [x for x in _state.get("allowed") or [] if _norm_id(x) != uid]
        _state["pending"] = pending
        _state["blocked"] = blocked
        _state["allowed"] = allowed
        _clear_guest_replies(uid)
        try:
            _save_locked()
        except Exception as e:
            return False, str(e)
    return True, ""


def remove_allowed(user_id: str) -> Tuple[bool, str]:
    """Убрать из разрешённых и занести в blocked (не вернётся без ручного approve с blocked)."""
    uid = _norm_id(user_id)
    _load()
    with _lock:
        allowed = [x for x in _state.get("allowed") or [] if _norm_id(x) != uid]
        blocked = list(_state.get("blocked") or [])
        if uid not in blocked:
            blocked.append(uid)
        _state["allowed"] = allowed
        _state["blocked"] = blocked
        _clear_guest_replies(uid)
        try:
            _save_locked()
        except Exception as e:
            return False, str(e)
    return True, ""


def snapshot() -> Dict[str, Any]:
    _load()
    with _lock:
        gr = _state.get("guest_replies")
        return {
            "enabled": is_approval_required(),
            "allowed": list(_state.get("allowed") or []),
            "blocked": list(_state.get("blocked") or []),
            "pending": list(_state.get("pending") or []),
            "guest_reply_quota": guest_reply_quota(),
            "guest_replies": dict(gr) if isinstance(gr, dict) else {},
        }


def admin_access_keyboard():
    """Кнопки: pending → одобрить/отклонить; allowed → отозвать."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    s = snapshot()
    rows: List[List[Any]] = []
    for row in (s.get("pending") or [])[:12]:
        if not isinstance(row, dict):
            continue
        uid = str(row.get("user_id") or "").strip()
        if not uid:
            continue
        label_ok = f"✅ {uid}"
        if len(label_ok) > 58:
            label_ok = f"✅ {uid[:12]}…"
        rows.append(
            [
                InlineKeyboardButton(text=label_ok, callback_data=f"acc:ok:{uid}"),
                InlineKeyboardButton(text="⛔", callback_data=f"acc:no:{uid}"),
            ]
        )
    for uid in (s.get("allowed") or [])[:12]:
        uid = str(uid).strip()
        if not uid:
            continue
        label = f"🗑 {uid}"
        if len(label) > 64:
            label = f"🗑 {uid[:10]}…"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"acc:rm:{uid}")])
    if not rows:
        rows.append([InlineKeyboardButton(text="— нет записей —", callback_data="acc:nop")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_admin_panel_html() -> str:
    from core.telegram_ui import esc

    s = snapshot()
    gq = int(s.get("guest_reply_quota") or 0)
    out = [
        "👥 <b>Доступ в личку</b>",
        "",
        "<blockquote>",
        f"Режим заявок: <b>{'вкл' if s['enabled'] else 'выкл'}</b> (<code>USER_ACCESS_APPROVAL_REQUIRED</code>)",
        f"Пробные ответы до одобрения: <b>{gq}</b> (<code>USER_ACCESS_GUEST_REPLY_QUOTA</code>, 0 = только ожидание)",
        "</blockquote>",
        "",
    ]
    pend = s.get("pending") or []
    pend_lines: List[str]
    if pend:
        pend_lines = []
        for row in pend[:20]:
            if not isinstance(row, dict):
                continue
            uid = row.get("user_id")
            un = row.get("username") or ""
            fn = row.get("full_name") or ""
            pend_lines.append(f"• <code>{esc(uid)}</code> {esc(fn)} @{esc(un)}")
    else:
        pend_lines = ["<i>Нет ожидающих заявок.</i>"]
    out.extend(["⏳ <b>Ожидают</b>", "", "<blockquote>", *pend_lines, "</blockquote>", ""])
    gr = s.get("guest_replies") if isinstance(s.get("guest_replies"), dict) else {}
    if gr and gq > 0:
        gr_lines = [f"• <code>{esc(uid_key)}</code>: {cnt}/{gq}" for uid_key, cnt in list(gr.items())[:20]]
        out.extend(
            [
                "🔢 <b>Гостевые ответы</b> <i>(фрагменты за ход)</i>",
                "",
                "<blockquote>",
                *gr_lines,
                "</blockquote>",
                "",
            ]
        )
    allow = s.get("allowed") or []
    if allow:
        out.extend(
            [
                "✅ <b>Разрешены</b>",
                "",
                "<blockquote>",
                f"({len(allow)}) <code>{esc(', '.join(str(x) for x in allow[:25]))}</code>",
                "</blockquote>",
                "",
            ]
        )
    out.append("<blockquote>Команда с кнопками: <code>/admin_access</code></blockquote>")
    return "\n".join(out)


def _private_user_names(from_user: Any) -> Tuple[Optional[str], str]:
    un = ((getattr(from_user, "username", None) or "") or "").strip() or None
    fn = " ".join(
        p
        for p in (
            (getattr(from_user, "first_name", None) or ""),
            (getattr(from_user, "last_name", None) or ""),
        )
        if p
    ).strip()
    return un, fn


def is_bot_self_private_actor(message: Any, user_id: str, bot_user_id: Any) -> bool:
    """True when private DM update is from the bot account (callbacks on bot messages)."""
    fu = getattr(message, "from_user", None)
    bid = bot_user_id
    return (bid is not None and user_id == str(bid)) or (
        fu is not None
        and getattr(fu, "is_bot", False)
        and user_id == str(getattr(fu, "id", ""))
    )


async def enforce_private_dm_access(
    message: Any,
    *,
    user_id: str,
    bot_user_id: Any = None,
    is_admin: bool = False,
    notify_new_request: Optional[Callable[[str, Optional[str], str], Awaitable[None]]] = None,
) -> bool:
    """
    Private DM access gate before heavy work (voice STT, orchestrator).

    Returns True if processing should continue; False if user was notified and handler must return.
    """
    if getattr(message, "chat", None) is None or getattr(message.chat, "type", "") != "private":
        return True
    if is_bot_self_private_actor(message, user_id, bot_user_id):
        return True
    if not is_approval_required() or is_admin:
        return True
    fu = getattr(message, "from_user", None)
    dec = evaluate_private_user(user_id, is_admin=False)
    if dec == "blocked":
        await message.answer(private_blocked_message())
        return False
    gq = guest_reply_quota()
    if gq > 0 and dec in ("pending", "enqueue"):
        if guest_replies_used(user_id) >= gq:
            await message.answer(guest_quota_exhausted_message())
            return False
        if dec == "enqueue":
            un, fn = _private_user_names(fu)
            is_new = enqueue_pending(user_id, username=un, full_name=fn or "")
            if is_new and notify_new_request is not None:
                await notify_new_request(user_id, un, fn)
    elif dec == "pending":
        await message.answer(private_pending_message())
        return False
    elif dec == "enqueue":
        un, fn = _private_user_names(fu)
        is_new = enqueue_pending(user_id, username=un, full_name=fn or "")
        if is_new and notify_new_request is not None:
            await notify_new_request(user_id, un, fn)
        await message.answer(private_pending_message())
        return False
    return True
