"""Команда /chat_style и кнопки переключения режима общения (conversation_style)."""
from __future__ import annotations

import logging

from typing import Any, Optional

from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from core.conversation_profiles import (
    VALID_STYLES,
    all_profiles_for_ui,
    keyboard_rows,
    normalize_conversation_style,
    profile_title_and_help,
)
from core.telegram_ui import esc
from core.telegram_util import sanitize_html


logger = logging.getLogger(__name__)

def _gid(message: Message) -> Optional[str]:
    if message.chat.type == ChatType.PRIVATE:
        return None
    return str(message.chat.id)


def conversation_style_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=text, callback_data=cb) for text, cb in line]
        for line in keyboard_rows()
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_chat_style_help_html(current_slug: str) -> str:
    cur = normalize_conversation_style(current_slug)
    title, _ = profile_title_and_help(cur)
    # В <pre> пустые строки между пунктами — в Telegram не сливается в один блок, как один длинный blockquote.
    prof_blocks: list[str] = []
    for slug, short, desc in all_profiles_for_ui():
        mark = "✓ " if slug == cur else "• "
        prof_blocks.append(f"{mark}{short} — {desc}")
    prof_pre = "\n\n".join(prof_blocks)

    return "\n".join(
        [
            "💬 <b>Стиль общения</b>",
            "",
            "<i>Тон и длина ответов в этом чате. Это не отдельная «модель» LLM.</i>",
            "",
            "<blockquote>",
            "Выберите, <b>как бот звучит в ответах</b>.",
            "",
            "Сохраняется отдельно для <b>лички</b> и для <b>каждой группы</b>.",
            "</blockquote>",
            "",
            "<blockquote>",
            f"📍 <b>Сейчас:</b> {esc(title)}",
            "</blockquote>",
            "",
            "🎚 <b>Профили</b>",
            "",
            f"<pre>{esc(prof_pre)}</pre>",
            "",
            "<blockquote>",
            "<i>Нажмите кнопку ниже или снова откройте</i> <code>/chat_style</code>",
            "</blockquote>",
        ]
    )


async def run_chat_style(layer: Any, message: Message) -> None:
    uid = str(message.from_user.id) if message.from_user else ""
    if not uid:
        return
    gid = _gid(message)
    rec = layer.orchestrator.behavior_store.load(uid, gid)
    cur = normalize_conversation_style(rec.get("conversation_style"))
    text = format_chat_style_help_html(cur)
    kb = conversation_style_keyboard()
    try:
        await message.answer(sanitize_html(text), parse_mode="HTML", reply_markup=kb)
    except Exception:
        await message.answer(sanitize_html(text[:3500]), parse_mode="HTML", reply_markup=kb)


async def handle_conversation_style_callback(layer: Any, callback: CallbackQuery, data: str) -> None:
    """data = cstyle:<slug>"""
    uid = str(callback.from_user.id) if callback.from_user else ""
    if not uid:
        await callback.answer("Нет user id", show_alert=True)
        return
    slug = (data.split(":", 1)[1] if ":" in data else "").strip().lower()
    if slug not in VALID_STYLES:
        await callback.answer("Неизвестный режим", show_alert=True)
        return
    msg = callback.message
    if not msg:
        await callback.answer("Нет сообщения", show_alert=True)
        return
    gid = _gid(msg)
    rec = layer.orchestrator.behavior_store.load(uid, gid)
    rec["conversation_style"] = slug
    layer.orchestrator.behavior_store.save(uid, gid, rec)
    title, help_txt = profile_title_and_help(slug)
    await callback.answer(f"Стиль: {title}", show_alert=False)
    try:
        await msg.answer(
            sanitize_html(f"✅ Стиль ответов: <b>{esc(title)}</b>\n\n{esc(help_txt)}"),
            parse_mode="HTML",
            reply_markup=conversation_style_keyboard(),
        )
    except Exception as e:
        logger.debug('%s optional failed: %s', 'conversation_style_telegram', e, exc_info=True)