"""
Telegram: загрузка site recipe кнопками (админы). Ранний перехват в InputLayer до тяжёлого document_intake.
"""
from __future__ import annotations

import logging
import os
import shutil
from typing import Any, Dict, Optional

from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from core.brain.constants import SILENT_DOCUMENT_USER_PROMPT
from core.site_recipe_engine import recipe_path_for_host, save_recipe
from core.site_recipe_upload_session import (
    append_item,
    cancel_session,
    defer_pop_normal,
    defer_register_normal,
    get_session,
    max_batch,
    session_active,
    start_session,
    try_parse_recipe_file,
)
from core.telegram_util import sanitize_html

logger = logging.getLogger(__name__)

CB_BEGIN = "sr:b"
CB_CANCEL = "sr:x"
CB_DONE = "sr:g"
CB_APPLY = "sr:y"
CB_PREFIX_NORMAL = "sr:n:"


def recipe_upload_intro_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Готово (применить список)", callback_data=CB_DONE),
                InlineKeyboardButton(text="❌ Отмена", callback_data=CB_CANCEL),
            ],
        ]
    )


def recipe_upload_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💾 Записать на диск", callback_data=CB_APPLY),
                InlineKeyboardButton(text="❌ Отмена", callback_data=CB_CANCEL),
            ],
        ]
    )


def recipe_fail_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📎 Обработать файл как обычно", callback_data=f"{CB_PREFIX_NORMAL}{token}"),
            ],
            [
                InlineKeyboardButton(text="❌ Выйти из режима рецепта", callback_data=CB_CANCEL),
            ],
        ]
    )


def _backup_if_exists(hostname: str) -> None:
    path = recipe_path_for_host(hostname)
    if os.path.isfile(path):
        bak = path + ".bak"
        try:
            shutil.copy2(path, bak)
        except OSError as e:
            logger.warning("[site_recipe_upload] backup failed %s: %s", path, e)


async def try_site_recipe_upload_early(
    layer: Any,
    message: Message,
    user_id: str,
    chat_id: str,
    file_context: Dict[str, Any],
    trace: Any,
) -> bool:
    """
    Если активна админ-сессия загрузки рецепта и сообщение — документ с локальным файлом,
    пытаемся принять JSON-рецепт. True = пайплайн должен завершиться (ответ уже отправлен).
    """
    if not layer._admin_module.is_admin(user_id):
        return False
    if not session_active(user_id, chat_id):
        return False
    if not isinstance(file_context, dict):
        return False
    if file_context.get("file_type") != "document":
        return False
    local_path = file_context.get("local_path")
    if not isinstance(local_path, str) or not local_path.strip():
        return False

    orig = str(file_context.get("original_name") or "file.json")
    low = orig.lower()
    mime = str(file_context.get("mime_type") or "").lower()
    if not low.endswith(".json") and "json" not in mime and "text/plain" not in mime:
        # Не похоже на JSON по типу — не трогаем здесь, пусть идёт обычный intake
        return False

    ok, norm, err, host = try_parse_recipe_file(local_path, orig)
    if ok:
        added, aerr = append_item(user_id, chat_id, host, norm, orig)
        if not added:
            try:
                await message.answer(f"Не добавлено: {aerr}")
            except Exception as e:
                logger.debug('%s optional failed: %s', 'site_recipe_telegram', e, exc_info=True)
            layer._file_intake.cleanup(local_path)
            return True
        sess = get_session(user_id, chat_id)
        n = len(sess.items) if sess else 0
        try:
            await message.answer(
                sanitize_html(
                    f"✅ Рецепт принят: <b>{host}</b> ({orig}). В очереди файлов: {n}/{max_batch()}.\n"
                    "Пришлите ещё JSON или нажмите «Готово»."
                ),
                parse_mode="HTML",
                reply_markup=recipe_upload_intro_keyboard(),
            )
        except Exception as e:
            logger.debug('%s optional failed: %s', 'site_recipe_telegram', e, exc_info=True)
        layer._file_intake.cleanup(local_path)
        return True

    token = defer_register_normal(user_id, chat_id, file_context)
    try:
        await message.answer(
            sanitize_html(
                "Это <b>не</b> валидный рецепт сайта (или не указан host):\n"
                f"<code>{err[:500]}</code>\n\n"
                "Можно обработать файл как обычный документ в чате или выйти из режима."
            ),
            parse_mode="HTML",
            reply_markup=recipe_fail_keyboard(token),
        )
    except Exception as e:
        logger.debug('%s optional failed: %s', 'site_recipe_telegram', e, exc_info=True)
    return True


async def handle_site_recipe_callback(layer: Any, callback: CallbackQuery) -> None:
    data = (getattr(callback, "data", None) or "").strip()
    uid = str(callback.from_user.id)
    if not layer._admin_module.is_admin(uid):
        await callback.answer("Нет доступа", show_alert=True)
        return

    if data == CB_BEGIN:
        if not callback.message:
            await callback.answer("Нет сообщения", show_alert=True)
            return
        chat_id = str(callback.message.chat.id)
        start_session(uid, chat_id)
        try:
            await callback.message.answer(
                sanitize_html(
                    "📄 <b>Режим загрузки рецепта сайта</b>\n\n"
                    "Пришлите один или несколько <code>.json</code> (поле <code>host</code> или имя файла "
                    "<code>домен.json</code>). Разбор идёт до тяжёлого PDF — только валидный рецепт.\n\n"
                    "Затем нажмите «Готово» → подтверждение записи в каталог рецептов."
                ),
                parse_mode="HTML",
                reply_markup=recipe_upload_intro_keyboard(),
            )
        except Exception as e:
            logger.debug('%s optional failed: %s', 'site_recipe_telegram', e, exc_info=True)
        await callback.answer("Режим включён")
        return

    if data == CB_CANCEL:
        if callback.message:
            cancel_session(uid, str(callback.message.chat.id))
        await callback.answer("Режим отменён, на диск ничего не записано")
        if callback.message:
            try:
                await callback.message.answer(
                    "Режим загрузки рецепта сброшен. Обычные документы снова обрабатываются как раньше."
                )
            except Exception as e:
                logger.debug('%s optional failed: %s', 'site_recipe_telegram', e, exc_info=True)
        return

    if not callback.message:
        await callback.answer("Нет сообщения", show_alert=True)
        return
    chat_id = str(callback.message.chat.id)

    if data == CB_DONE:
        sess = get_session(uid, chat_id)
        if not sess or not sess.items:
            await callback.answer("Нет файлов в очереди", show_alert=True)
            try:
                await callback.message.answer(
                    "Очередь пуста. Пришлите JSON или отмените режим.",
                    reply_markup=recipe_upload_intro_keyboard(),
                )
            except Exception as e:
                logger.debug('%s optional failed: %s', 'site_recipe_telegram', e, exc_info=True)
            return
        lines = ["<b>Проверка перед записью:</b>"]
        for host, _rec, fname in sess.items:
            lines.append(f"• <code>{host}</code> — {fname}")
        lines.append("\nЗапись идёт в каталог рецептов (пресет в коде не меняется). Перед заменой создаётся <code>.bak</code>.")
        try:
            await callback.message.answer(
                sanitize_html("\n".join(lines)),
                parse_mode="HTML",
                reply_markup=recipe_upload_confirm_keyboard(),
            )
        except Exception as e:
            logger.debug('%s optional failed: %s', 'site_recipe_telegram', e, exc_info=True)
        await callback.answer()
        return

    if data == CB_APPLY:
        sess = get_session(uid, chat_id)
        if not sess or not sess.items:
            await callback.answer("Очередь пуста", show_alert=True)
            return
        saved: list[str] = []
        errors: list[str] = []
        for host, rec, fname in sess.items:
            try:
                _backup_if_exists(host)
                rec_write = dict(rec)
                rec_write["host"] = host
                if save_recipe(host, rec_write):
                    saved.append(host)
                else:
                    errors.append(f"{host}: save_recipe=false")
            except Exception as e:
                errors.append(f"{host}: {e}")
        cancel_session(uid, chat_id)
        parts = []
        if saved:
            parts.append("✅ Записано: " + ", ".join(f"<code>{h}</code>" for h in saved))
        if errors:
            parts.append("⚠️ Ошибки: " + "; ".join(errors)[:800])
        parts.append("Откат: удалить файл в каталоге рецептов или восстановить из <code>.bak</code>.")
        try:
            await callback.message.answer(sanitize_html("\n".join(parts)), parse_mode="HTML")
        except Exception as e:
            logger.debug('%s optional failed: %s', 'site_recipe_telegram', e, exc_info=True)
        await callback.answer("Готово")
        return

    if data.startswith(CB_PREFIX_NORMAL):
        token = data[len(CB_PREFIX_NORMAL) :].strip().lower()
        fc = defer_pop_normal(token, uid)
        if not fc:
            await callback.answer("Файл устарел — пришлите снова", show_alert=True)
            return
        await callback.answer("Обрабатываю как обычный документ…")
        try:
            await layer._process_message(
                callback.message,
                synthetic_payload=SILENT_DOCUMENT_USER_PROMPT,
                file_context_override=fc,
                actor_user_id=uid,
            )
        except Exception as e:
            logger.exception("site_recipe defer normal: %s", e)
            lp = fc.get("local_path")
            layer._file_intake.cleanup(lp)
        return

    await callback.answer("Неизвестное действие", show_alert=True)
