"""
Feedback buttons — inline-кнопки под ответом бота.

Две кнопки:
- 👍 Хороший ответ — повышает effectiveness_score уроков
- 👎 Плохой ответ — понижает, генерирует reflexion-урок
- 🐛 Баг — собирает диагностику и отправляет админу в ЛС

Callback data: fb:{action}:{user_id}
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional

from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

_FB_DEBOUNCE_SEC = max(0.5, float(os.getenv("FEEDBACK_BUTTON_DEBOUNCE_SEC", "2") or "2"))
_fb_last_applied: Dict[str, float] = {}

FB_PREFIX = "fb:"
ACTIONS = {
    "good": "👍 Хороший ответ",
    "bad": "👎 Плохой ответ",
    "bug": "🐛 Баг",
}


def build_feedback_keyboard(user_id: str) -> InlineKeyboardMarkup:
    """Собрать ряд кнопок обратной связи."""
    uid = str(user_id)
    buttons: List[InlineKeyboardButton] = []
    for action, label in ACTIONS.items():
        buttons.append(
            InlineKeyboardButton(text=label, callback_data=f"{FB_PREFIX}{action}:{uid}")
        )
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


def merge_with_reply_markup(
    existing: Optional[InlineKeyboardMarkup],
    user_id: str,
) -> InlineKeyboardMarkup:
    """Добавить ряд кнопок фидбека в конец существующей клавиатуры."""
    fb_kb = build_feedback_keyboard(user_id)
    if existing is None:
        return fb_kb
    rows = list(existing.inline_keyboard)
    rows.extend(fb_kb.inline_keyboard)
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def handle_feedback_callback(callback: CallbackQuery) -> None:
    """Обработчик нажатия на кнопку фидбека."""
    data = (getattr(callback, "data", None) or "").strip()
    if not data.startswith(FB_PREFIX):
        return

    inner = data[len(FB_PREFIX):]
    parts = inner.split(":", 1)
    if len(parts) < 1:
        await _answer(callback, "Некорректная кнопка", alert=True)
        return
    action = parts[0].strip()
    uid_from_data = parts[1].strip() if len(parts) > 1 else ""
    uid = str(callback.from_user.id)

    if uid != uid_from_data:
        await _answer(callback, "Это не ваша кнопка", alert=True)
        return

    if action not in ACTIONS:
        await _answer(callback, "Неизвестное действие", alert=True)
        return

    if action == "bug":
        # Баг: спрашиваем описание, не обрабатываем как фидбек
        from core.user_bug_report import set_pending

        chat_id = str(callback.message.chat.id)
        msg_id = callback.message.message_id
        un = getattr(callback.from_user, "username", "") or ""
        fn = getattr(callback.from_user, "full_name", "") or ""
        set_pending(uid, chat_id, msg_id, username=un, full_name=fn)
        await _answer(callback, "Опиши проблему одним сообщением ✍️")
        return

    # Ответить сразу для good/bad
    toasts = {
        "good": "Спасибо! Я старался 🙂",
        "bad": "Записал 👎",
    }
    deb_key = f"{uid}:{action}"
    now = time.monotonic()
    last = _fb_last_applied.get(deb_key, 0.0)
    if now - last < _FB_DEBOUNCE_SEC:
        return
    _fb_last_applied[deb_key] = now
    await _answer(callback, toasts.get(action, "Спасибо!"))
    asyncio.create_task(_process_feedback(action, uid, callback))


async def _answer(callback: CallbackQuery, text: str, alert: bool = False) -> None:
    try:
        from core.telegram_util import safe_callback_answer
        await safe_callback_answer(callback, text, show_alert=alert)
    except Exception:
        try:
            await callback.answer(text, show_alert=alert)
        except Exception as e:
            logger.debug('%s optional failed: %s', 'feedback_buttons', e, exc_info=True)
async def _process_feedback(
    action: str,
    user_id: str,
    callback: Optional[CallbackQuery] = None,
) -> None:
    """Фоновая обработка: experience + CDC + уроки."""
    try:
        from core.user_response_feedback import apply_user_rating
        from core.user_correction_bus import format_learning_ack_from_rating

        score = 1 if action == "good" else -1
        behavior_store = None
        try:
            from core.behavior_store import BehaviorStore

            behavior_store = BehaviorStore()
        except Exception as e:
            logger.debug('%s optional failed: %s', 'feedback_buttons', e, exc_info=True)
        rep = apply_user_rating(
            user_id=user_id,
            score=score,
            behavior_store=behavior_store,
            source="telegram_button",
        )
        logger.info("[feedback] action=%s user=%s applied=%s", action, user_id, rep.get("applied"))
        if action == "bad" and callback and callback.message:
            ack = format_learning_ack_from_rating(rep)
            if ack:
                try:
                    from core.telegram_util import sanitize_html

                    await callback.message.answer(
                        sanitize_html(ack),
                        parse_mode="HTML",
                    )
                except Exception as e:
                    logger.debug("feedback ack message: %s", e)
    except Exception as e:
        logger.debug("[feedback] error: %s", e)
