"""
User Bug Report — простая кнопка «Баг» вместо «Потерялся».

Flow:
1. Пользователь нажимает 🐛 Баг → callback → бот спрашивает «Что не так?»
2. Пользователь пишет описание → InputLayer видит pending_bug →
   собирает diagnostic bundle + отправляет админу в ЛС
3. Pending очищается
"""
from __future__ import annotations

import html
import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# TTL для pending-состояния (сек)
_BUG_PENDING_TTL_SEC = int(os.getenv("BUG_PENDING_TTL_SEC", "600"))

# {(user_id, chat_id): {"reply_to_msg_id": ..., "username": ..., "full_name": ..., "ts": float}}
_pending: Dict[tuple, Dict[str, Any]] = {}
_BOT_INSTANCE: Any = None


def register_bot(bot: Any) -> None:
    global _BOT_INSTANCE
    _BOT_INSTANCE = bot


def set_pending(
    user_id: str,
    chat_id: str,
    reply_to_message_id: int,
    username: str = "",
    full_name: str = "",
) -> None:
    """Запомнить что пользователь хочет отправить баг-репорт (с TTL)."""
    _purge_stale()
    key = (user_id, chat_id)
    _pending[key] = {
        "reply_to_msg_id": reply_to_message_id,
        "username": username,
        "full_name": full_name,
        "ts": time.time(),
    }
    logger.info("[bug_report] pending set for user=%s chat=%s ttl=%ds", user_id, chat_id, _BUG_PENDING_TTL_SEC)


def pop_pending(user_id: str, chat_id: str) -> Optional[Dict[str, Any]]:
    """Забрать и удалить pending (с проверкой TTL)."""
    _purge_stale()
    key = (user_id, chat_id)
    return _pending.pop(key, None)


def has_pending(user_id: str, chat_id: str) -> bool:
    _purge_stale()
    return (user_id, chat_id) in _pending


# Одно слово / короткая реплика — поправка или эмоция, не описание бага.
_BUG_REPORT_CANCEL_EXACT = frozenset(
    {
        "мусор",
        "бред",
        "хрень",
        "фигня",
        "чушь",
        "отстой",
        "junk",
        "garbage",
        "nonsense",
        "wtf",
    }
)


def should_cancel_bug_report_pending(text: str) -> bool:
    """
    Сообщение после кнопки «Баг» — не отправлять как баг-репорт:
    поправки («неправильно понял»), ругань («мусор»), пустота.
    """
    raw = (text or "").strip()
    if not raw:
        return True
    low = raw.lower()
    if low in _BUG_REPORT_CANCEL_EXACT:
        return True
    if len(low.split()) <= 2 and len(low) <= 32:
        if any(tok in low for tok in ("мусор", "бред", "хрень", "фигня", "чушь", "отстой")):
            return True
    try:
        from core.dialogue_feedback_signals import user_feedback_likely

        if user_feedback_likely(raw):
            return True
    except Exception as e:
        logger.debug('%s optional failed: %s', 'user_bug_report', e, exc_info=True)
    return False


def _purge_stale() -> None:
    """Удалить все pending, у которых истёк TTL."""
    now = time.time()
    stale = [k for k, v in _pending.items() if now - v.get("ts", 0) > _BUG_PENDING_TTL_SEC]
    for k in stale:
        _pending.pop(k, None)
        logger.debug("[bug_report] purge stale pending %s (ttl=%ds)", k, _BUG_PENDING_TTL_SEC)


async def collect_and_send(
    user_id: str,
    chat_id: str,
    description: str,
    reply_to_msg_id: int,
    username: str,
    full_name: str,
) -> None:
    """Собрать диагностику и отправить админу."""
    try:
        from core.event_bus import bus
        from core.event_bus import BugReportCollectedEvent

        # Emit событие — BugContextGatherer подхватит
        await bus.emit_await("bug_report.collected", {
            "user_id": user_id,
            "description": description,
            "username": username,
            "chat_id": chat_id,
        })
        # После emit_await в payload добавится diagnostic_context от gatherer
        # Забираем последнее событие для диагностики
        ctx_data = {}
        try:
            recent = bus.history(n=1, event_type="bug_report.collected")
            if recent:
                ev = recent[-1]
                ctx_data = ev.data.get("diagnostic_context", {})
        except Exception as e:
            logger.debug('%s optional failed: %s', 'user_bug_report', e, exc_info=True)
        from core.admin_module import admin_user_ids

        admin_ids = list(admin_user_ids())
        if not admin_ids:
            logger.warning("[bug_report] нет ADMIN_USER_IDS — некому отправить")
            return

        bundle_text = _build_bug_text(
            user_id=user_id,
            chat_id=chat_id,
            description=description,
            username=username,
            full_name=full_name,
            diagnostic_context=ctx_data or None,
        )

        bot = _BOT_INSTANCE
        if bot is None:
            logger.warning("[bug_report] bot not registered")
            return

        from core.telegram_ui import split_html_message

        parts = split_html_message(bundle_text, limit=4000)
        for admin_id in admin_ids:
            try:
                for part in parts:
                    await bot.send_message(
                        chat_id=int(admin_id),
                        text=part,
                        parse_mode="HTML",
                    )
                logger.info("[bug_report] sent to admin=%s from user=%s", admin_id, user_id)
            except Exception as e:
                logger.warning("[bug_report] failed to send to admin=%s: %s", admin_id, e)
    except Exception as e:
        logger.error("[bug_report] collect_and_send error: %s", e)


def _build_bug_text(
    user_id: str,
    chat_id: str,
    description: str,
    username: str,
    full_name: str,
    diagnostic_context: Optional[Dict[str, Any]] = None,
) -> str:
    un = html.escape(f"@{username}" if username else "—")
    fn = html.escape(full_name or "—")
    desc = html.escape((description or "").strip() or "—")
    uid = html.escape(str(user_id))
    cid = html.escape(str(chat_id))
    lines = [
        "🐛 <b>Баг-репорт от пользователя</b>",
        "",
        f"<b>User ID:</b> <code>{uid}</code>",
        f"<b>Chat ID:</b> <code>{cid}</code>",
        f"<b>Username:</b> {un}",
        f"<b>Имя:</b> {fn}",
        "",
        "<b>Описание проблемы:</b>",
        desc,
    ]

    # Диагностический контекст от EventBus healers
    if diagnostic_context:
        lines.extend(["", "🤖 <b>Авто-диагностика:</b>"])
        counters = diagnostic_context.get("monitor_key_counters", {})
        if counters:
            parts = []
            for k, v in counters.items():
                short = k.replace("openrouter_", "or_").replace("_total", "")
                parts.append(f"{short}={v}")
            lines.append("Мониторинг: " + ", ".join(parts))

        latency = diagnostic_context.get("latency_p95", {})
        if latency:
            lines.append(f"P95 задержки: {latency}")

        llm = diagnostic_context.get("llm_recent", {})
        if llm:
            ok_n = llm.get("ok", 0)
            fail_n = llm.get("fail", 0)
            lat = llm.get("avg_latency_ms", 0)
            lines.append(f"LLM (5 мин): ok={ok_n} fail={fail_n} avg_lat={lat:.0f}ms")

        recent_events = diagnostic_context.get("recent_events_summary", [])
        if recent_events:
            last = recent_events[-3:]
            ev_lines = []
            for e in last:
                et = e.get("event_type", "?")
                ts = e.get("ts", "?")
                ev_lines.append(f"  {ts} {et}")
            lines.append("Последние события:")
            lines.extend(ev_lines)

    from core.telegram_util import sanitize_html
    return sanitize_html("\n".join(lines))


# Pending-совместимость
def clear_fn(user_id: str, chat_id: str) -> bool:
    return pop_pending(user_id, chat_id) is not None


__all__ = [
    "register_bot",
    "set_pending",
    "pop_pending",
    "has_pending",
    "collect_and_send",
    "clear_fn",
]
