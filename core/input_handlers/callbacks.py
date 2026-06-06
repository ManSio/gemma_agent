from __future__ import annotations

import logging
from typing import Any, List, Optional

from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from core.input_handlers.help_payload import (
    HELP_ADMIN_ACTIONS,
    HELP_PATCH_ACTIONS,
    HELP_USER_ACTIONS,
    build_help_payload,
    collect_command_catalog,
)
from core.brain.post_module_gen_ui import first_slash_from_module_disk
from core.manifest_buttons import parse_mbtn_callback, resolve_button_simulated_text
from core.monitoring import MONITOR
from core.runtime_telegram_settings import toggle_by_id
from core.input_handlers.help_payload import admin_stats_button_rows
from core.input_handlers.slash_button_dispatch import dispatch_slash_from_button
from core.telegram_util import safe_callback_answer, sanitize_html


logger = logging.getLogger(__name__)

def _qualify_command_for_group_chat(text: str, message: Message, layer: Any) -> str:
    """В супергруппе без @бот команда часто не доходит — подставляем /cmd@username."""
    t = (text or "").strip()
    if not t.startswith("/"):
        return t
    if not layer._is_group_chat(message):
        return t
    un = (getattr(layer, "_bot_username", None) or "").strip().lower()
    if not un:
        return t
    parts = t.split(None, 1)
    first = parts[0]
    if "@" in first:
        return t
    rest = parts[1] if len(parts) > 1 else ""
    qualified = f"{first}@{un}"
    return f"{qualified} {rest}".strip() if rest else qualified


async def _send_help_pages(
    message: Any,
    chunks: List[str],
    kb: Optional[InlineKeyboardMarkup],
    *,
    bot: Any,
) -> None:
    """Все страницы справки (HTML). InaccessibleMessage без edit_text — шлём новое сообщение в тот же чат."""
    try:
        from aiogram.exceptions import TelegramBadRequest
    except ImportError:
        TelegramBadRequest = Exception  # type: ignore

    chat_id = getattr(getattr(message, "chat", None), "id", None)
    if chat_id is None:
        return

    if not chunks:
        try:
            await bot.send_message(chat_id, sanitize_html("Справка пуста."), reply_markup=kb, parse_mode="HTML")
        except Exception as e:
            logger.debug('%s optional failed: %s', 'callbacks', e, exc_info=True)
        return

    main = sanitize_html(str(chunks[0])[:3900])
    can_edit = isinstance(message, Message) and getattr(message, "text", None) is not None

    if can_edit:
        try:
            await message.edit_text(main, reply_markup=kb, parse_mode="HTML")
        except TelegramBadRequest as e:
            em = str(e).lower()
            if "message is not modified" in em or "message_not_modified" in em:
                pass
            else:
                try:
                    await bot.send_message(chat_id, main, reply_markup=kb, parse_mode="HTML")
                except Exception as e:
                    logger.debug('%s optional failed: %s', 'callbacks', e, exc_info=True)
        except Exception:
            try:
                await bot.send_message(chat_id, main, reply_markup=kb, parse_mode="HTML")
            except Exception as e:
                logger.debug('%s optional failed: %s', 'callbacks', e, exc_info=True)
    else:
        try:
            await bot.send_message(chat_id, main, reply_markup=kb, parse_mode="HTML")
        except Exception as e:
            logger.debug('%s optional failed: %s', 'callbacks', e, exc_info=True)
    for extra in chunks[1:]:
        try:
            await bot.send_message(chat_id, sanitize_html(str(extra)[:3900]), parse_mode="HTML")
        except Exception as e:
            logger.debug('%s optional failed: %s', 'callbacks', e, exc_info=True)
def register(layer: Any) -> None:
    dp = layer.dp

    @dp.callback_query()
    async def handle_admin_callback(callback: CallbackQuery):
        data = getattr(callback, "data", "") or ""
        uid = str(callback.from_user.id)
        if data.startswith("gs:stop:"):
            from core.telegram_stream_reply import request_chat_cancel

            cid = data.split(":", 2)[-1].strip()
            if cid and str(callback.message.chat.id) != cid:
                await safe_callback_answer(callback, "Не этот чат", show_alert=True)
                return
            stopped = await request_chat_cancel(cid or str(callback.message.chat.id))
            await safe_callback_answer(
                callback,
                "Останавливаю ответ…" if stopped else "Сейчас нечего останавливать",
            )
            return
        # Feedback buttons
        if data.startswith("fb:"):
            from core.feedback_buttons import handle_feedback_callback
            await handle_feedback_callback(callback)
            return
        if data.startswith("pgen:"):
            parts = data.split(":", 2)
            if len(parts) < 3:
                await safe_callback_answer(callback, "Некорректная кнопка", show_alert=True)
                return
            action = parts[1].strip().lower()
            folder = parts[2].strip()
            if not folder or action not in {"t", "r"}:
                await safe_callback_answer(callback, "Некорректная кнопка", show_alert=True)
                return
            reg = getattr(layer, "plugin_registry", None)
            if reg is None:
                await safe_callback_answer(callback, "Реестр недоступен", show_alert=True)
                return
            mod_root = getattr(reg, "modules_path", None)
            if mod_root is None:
                await safe_callback_answer(callback, "Путь к модулям недоступен", show_alert=True)
                return
            if action == "t":
                text = first_slash_from_module_disk(mod_root, folder)
                if not text:
                    await safe_callback_answer(callback, "Команда не найдена в module.json", show_alert=True)
                    return
                await safe_callback_answer(callback)
                payload = _qualify_command_for_group_chat(text, callback.message, layer)
                await layer._process_message(
                    callback.message, synthetic_payload=payload, actor_user_id=uid
                )
                return
            res = reg.hot_install_module(folder)
            if res.get("success"):
                await safe_callback_answer(callback, "Модуль в реестре", show_alert=False)
            else:
                await safe_callback_answer(callback, str(res.get("error") or "Ошибка загрузки")[:180], show_alert=True)
            return

        if data.startswith("cstyle:"):
            from core.input_handlers.conversation_style_telegram import handle_conversation_style_callback

            await handle_conversation_style_callback(layer, callback, data)
            return

        if data.startswith("udoc:"):
            from core.user_document_pending import handle_udoc_callback

            await handle_udoc_callback(layer, callback)
            return

        if data.startswith("sr:"):
            from core.input_handlers.site_recipe_telegram import handle_site_recipe_callback

            await handle_site_recipe_callback(layer, callback)
            return

        if data.startswith("admseed:"):
            if not layer._admin_module.is_admin(uid):
                await safe_callback_answer(callback, "Нет доступа", show_alert=True)
                return
            if not callback.message:
                await safe_callback_answer(callback, "Нет сообщения", show_alert=True)
                return
            mode = (data.split(":", 1)[1] if ":" in data else "").strip().lower()
            from core.runtime_config_seed import (
                format_runtime_seed_report_ru,
                seed_runtime_config_from_examples,
            )

            if mode not in ("miss", "missing", "", "dir", "force", "directive", "all", "full"):
                await safe_callback_answer(callback, "Неизвестный режим", show_alert=True)
                return
            await safe_callback_answer(callback)
            if mode in ("miss", "missing", ""):
                rep = seed_runtime_config_from_examples(force_directive=False, force_operator_rules=False)
            elif mode in ("dir", "force", "directive"):
                rep = seed_runtime_config_from_examples(force_directive=True, force_operator_rules=False)
            else:
                rep = seed_runtime_config_from_examples(force_directive=True, force_operator_rules=True)
            try:
                await callback.message.answer(
                    sanitize_html("<b>📎 Сиды runtime</b>\n\n" + format_runtime_seed_report_ru(rep)),
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.debug('%s optional failed: %s', 'callbacks', e, exc_info=True)
            return

        if data in ("mathamb:skip", "mathamb:calc"):
            if not callback.message:
                await safe_callback_answer(callback, "Нет сообщения", show_alert=True)
                return
            from core.intent_heuristics import merge_routing_prefs_from_turn
            from core.math_clarify_pending import pop_pending

            chat_id = str(callback.message.chat.id)
            gid = chat_id if callback.message.chat.type != ChatType.PRIVATE else None
            pending = pop_pending(uid, chat_id)
            if not pending:
                await safe_callback_answer(callback, "Нет текста для повтора — отправьте сообщение снова", show_alert=True)
                return
            rec = layer.orchestrator.behavior_store.load(uid, gid)
            if data == "mathamb:skip":
                merge_routing_prefs_from_turn(rec, "калькулятор не нужен")
                layer.orchestrator.behavior_store.save(uid, gid, rec)
                await safe_callback_answer(callback, "Ок, без калькулятора…")
                synthetic = pending
            else:
                merge_routing_prefs_from_turn(rec, "можно калькулятор")
                layer.orchestrator.behavior_store.save(uid, gid, rec)
                await safe_callback_answer(callback, "Считаю…")
                synthetic = (
                    pending
                    + "\n\nПосчитай только явные арифметические выражения в тексте выше "
                    "(цифры и + − * /), без переинтерпретации сюжета."
                )
            await layer._process_message(
                callback.message, synthetic_payload=synthetic, actor_user_id=uid
            )
            return

        if data in ("factcfm:y", "factcfm:n"):
            if not callback.message:
                await safe_callback_answer(callback, "Нет сообщения", show_alert=True)
                return
            try:
                from core.telegram_inbound_dedup import should_skip_duplicate_callback

                if should_skip_duplicate_callback(getattr(callback, "id", None)):
                    MONITOR.inc("telegram_inbound_dedup_skip_total")
                    await safe_callback_answer(callback)
                    return
            except Exception as e:
                logger.debug("telegram_inbound_dedup callback: %s", e)
            await safe_callback_answer(callback)
            sym = "да" if data.endswith(":y") else "нет"
            await layer._process_message(
                callback.message, synthetic_payload=sym, actor_user_id=uid
            )
            return

        if data.startswith("factask:"):
            if not callback.message:
                await safe_callback_answer(callback, "Нет сообщения", show_alert=True)
                return
            if data.startswith("factask:sk:"):
                await safe_callback_answer(callback, "Ок")
                return
            if data.startswith("factask:tx:"):
                rest = data[len("factask:tx:") :].strip()
                if not rest:
                    await safe_callback_answer(callback, "Пусто", show_alert=True)
                    return
                up = rest.upper()
                if up in ("EUR", "USD", "BYN", "RUB", "GBP", "UAH", "KZT", "PLN"):
                    payload = f"Моя валюта {up}"
                elif "/" in rest or rest.upper().startswith("UTC") or rest.lower().startswith("europe/"):
                    payload = f"Мой часовой пояс {rest}"
                else:
                    payload = f"Я из {rest}"
                await safe_callback_answer(callback)
                await layer._process_message(
                    callback.message, synthetic_payload=payload, actor_user_id=uid
                )
                return

        parsed_mbtn = parse_mbtn_callback(data)
        if parsed_mbtn:
            mod_key, btn_name = parsed_mbtn
            text = resolve_button_simulated_text(layer.plugin_registry, mod_key, btn_name)
            if text:
                await safe_callback_answer(callback)
                await layer._process_message(
                    callback.message, synthetic_payload=text, actor_user_id=uid
                )
                return
            await safe_callback_answer(callback, "Действие недоступно", show_alert=True)
            return
        if data.startswith("hc:"):
            if not callback.message:
                await safe_callback_answer(callback, "Нет контекста сообщения", show_alert=True)
                return
            raw_i = data.split(":", 1)[1].strip() if ":" in data else ""
            try:
                gi = int(raw_i)
            except ValueError:
                await safe_callback_answer(callback, "Некорректная кнопка", show_alert=True)
                return
            catalog = collect_command_catalog(layer.plugin_registry)
            if gi < 0 or gi >= len(catalog):
                await safe_callback_answer(callback, "Список устарел — откройте /help → Модули снова", show_alert=True)
                return
            trigger = (catalog[gi].get("trigger") or "").strip()
            if not trigger:
                await safe_callback_answer(callback, "Пустая команда", show_alert=True)
                return
            payload = _qualify_command_for_group_chat(trigger, callback.message, layer)
            await safe_callback_answer(callback)
            if await dispatch_slash_from_button(layer, callback.message, payload, actor_user_id=uid):
                return
            await layer._process_message(
                callback.message, synthetic_payload=payload, actor_user_id=uid
            )
            return
        if data.startswith("hu:"):
            if not callback.message:
                await safe_callback_answer(callback, "Нет контекста сообщения", show_alert=True)
                return
            raw_i = data.split(":", 1)[1].strip() if ":" in data else ""
            try:
                gi = int(raw_i)
            except ValueError:
                await safe_callback_answer(callback, "Некорректная кнопка", show_alert=True)
                return
            if gi < 0 or gi >= len(HELP_USER_ACTIONS):
                await safe_callback_answer(callback, "Список устарел — откройте /help снова", show_alert=True)
                return
            trigger = (HELP_USER_ACTIONS[gi][0] or "").strip()
            if not trigger:
                await safe_callback_answer(callback, "Пустая команда", show_alert=True)
                return
            payload = _qualify_command_for_group_chat(trigger, callback.message, layer)
            await safe_callback_answer(callback)
            if await dispatch_slash_from_button(layer, callback.message, payload, actor_user_id=uid):
                return
            await layer._process_message(
                callback.message, synthetic_payload=payload, actor_user_id=uid
            )
            return
        if data.startswith("hs:"):
            if not layer._admin_module.is_admin(uid):
                await safe_callback_answer(callback, "Нет доступа", show_alert=True)
                return
            if not callback.message:
                await safe_callback_answer(callback, "Нет контекста сообщения", show_alert=True)
                return
            from core.help_catalog_sync import HELP_STATS_ACTIONS

            raw_i = data.split(":", 1)[1].strip() if ":" in data else ""
            if raw_i == "rep_json":
                trigger = "/admin_reputation_json"
                payload = _qualify_command_for_group_chat(trigger, callback.message, layer)
                await safe_callback_answer(callback)
                if not await dispatch_slash_from_button(layer, callback.message, payload, actor_user_id=uid):
                    await callback.message.answer(
                        "Отчёт не запустился. Введите: <code>/admin_reputation_json</code>",
                        parse_mode="HTML",
                    )
                return
            try:
                gi = int(raw_i)
            except ValueError:
                await safe_callback_answer(callback, "Некорректная кнопка", show_alert=True)
                return
            if gi < 0 or gi >= len(HELP_STATS_ACTIONS):
                await safe_callback_answer(callback, "Список устарел — откройте /help → Статистика", show_alert=True)
                return
            trigger = (HELP_STATS_ACTIONS[gi][0] or "").strip()
            if not trigger:
                await safe_callback_answer(callback, "Пустая команда", show_alert=True)
                return
            payload = _qualify_command_for_group_chat(trigger, callback.message, layer)
            await safe_callback_answer(callback)
            if not await dispatch_slash_from_button(layer, callback.message, payload, actor_user_id=uid):
                await callback.message.answer(
                    "Отчёт не запустился. Введите команду вручную: "
                    f"<code>{sanitize_html(trigger)}</code>",
                    parse_mode="HTML",
                )
            return
        if data.startswith("hp:"):
            if not layer._admin_module.is_admin(uid):
                await safe_callback_answer(callback, "Нет доступа", show_alert=True)
                return
            if not callback.message:
                await safe_callback_answer(callback, "Нет контекста сообщения", show_alert=True)
                return
            raw_i = data.split(":", 1)[1].strip() if ":" in data else ""
            try:
                gi = int(raw_i)
            except ValueError:
                await safe_callback_answer(callback, "Некорректная кнопка", show_alert=True)
                return
            if gi < 0 or gi >= len(HELP_PATCH_ACTIONS):
                await safe_callback_answer(callback, "Список устарел — откройте /help → Латки снова", show_alert=True)
                return
            trigger = (HELP_PATCH_ACTIONS[gi][0] or "").strip()
            if not trigger:
                await safe_callback_answer(callback, "Пустая команда", show_alert=True)
                return
            payload = _qualify_command_for_group_chat(trigger, callback.message, layer)
            await safe_callback_answer(callback)
            if await dispatch_slash_from_button(layer, callback.message, payload, actor_user_id=uid):
                return
            await layer._process_message(
                callback.message, synthetic_payload=payload, actor_user_id=uid
            )
            return
        if data.startswith("ha:"):
            if not layer._admin_module.is_admin(uid):
                await safe_callback_answer(callback, "Нет доступа", show_alert=True)
                return
            if not callback.message:
                await safe_callback_answer(callback, "Нет контекста сообщения", show_alert=True)
                return
            raw_i = data.split(":", 1)[1].strip() if ":" in data else ""
            try:
                gi = int(raw_i)
            except ValueError:
                await safe_callback_answer(callback, "Некорректная кнопка", show_alert=True)
                return
            if gi < 0 or gi >= len(HELP_ADMIN_ACTIONS):
                await safe_callback_answer(callback, "Список устарел — откройте /help → Админ снова", show_alert=True)
                return
            trigger = (HELP_ADMIN_ACTIONS[gi][0] or "").strip()
            if not trigger:
                await safe_callback_answer(callback, "Пустая команда", show_alert=True)
                return
            payload = _qualify_command_for_group_chat(trigger, callback.message, layer)
            await safe_callback_answer(callback)
            if await dispatch_slash_from_button(layer, callback.message, payload, actor_user_id=uid):
                return
            await layer._process_message(
                callback.message, synthetic_payload=payload, actor_user_id=uid
            )
            return
        if data.startswith("rc:"):
            parts = data.split(":")
            if len(parts) < 3 or parts[1] != "r":
                await safe_callback_answer(callback, "Некорректные данные", show_alert=True)
                return
            rid = (parts[2] or "").strip().lower()
            from core.response_text_cache import lookup_record

            ent = lookup_record(rid)
            if not ent or str(ent.get("user_id") or "") != uid:
                await safe_callback_answer(callback, "Запись устарела или не ваша", show_alert=True)
                return
            if not callback.message:
                await safe_callback_answer(callback, "Нет сообщения", show_alert=True)
                return
            await safe_callback_answer(callback, "Считаю заново…")
            await layer._process_message(
                callback.message,
                synthetic_payload=str(ent.get("replay_payload") or ""),
                cache_bypass=True,
                actor_user_id=uid,
            )
            return
        if data.startswith("acc:"):
            if not layer._admin_module.is_admin(uid):
                await safe_callback_answer(callback, "Нет доступа", show_alert=True)
                return
            parts = data.split(":")
            if len(parts) >= 2 and parts[1] == "nop":
                await safe_callback_answer(callback)
                return
            if len(parts) < 3:
                await safe_callback_answer(callback, "Некорректные данные", show_alert=True)
                return
            act, target = parts[1], parts[2]
            from core import access_gate as agate

            try:
                tid = int(target)
            except ValueError:
                await safe_callback_answer(callback, "Некорректный id", show_alert=True)
                return
            if act == "ok":
                ok, err = agate.approve(target)
                if ok:
                    try:
                        await callback.bot.send_message(tid, agate.private_approved_notice())
                    except Exception as e:
                        logger.debug('%s optional failed: %s', 'callbacks', e, exc_info=True)
                    await safe_callback_answer(callback, "Доступ подтверждён")
                else:
                    await safe_callback_answer(callback, err[:200] or "Ошибка", show_alert=True)
                return
            if act == "no":
                ok, err = agate.reject(target)
                if ok:
                    try:
                        await callback.bot.send_message(tid, agate.private_rejected_notice())
                    except Exception as e:
                        logger.debug('%s optional failed: %s', 'callbacks', e, exc_info=True)
                    await safe_callback_answer(callback, "Отклонено")
                else:
                    await safe_callback_answer(callback, err[:200] or "Ошибка", show_alert=True)
                return
            if act == "rm":
                ok, err = agate.remove_allowed(target)
                if ok:
                    try:
                        await callback.bot.send_message(tid, agate.private_removed_notice())
                    except Exception as e:
                        logger.debug('%s optional failed: %s', 'callbacks', e, exc_info=True)
                    await safe_callback_answer(callback, "Доступ отозван")
                else:
                    await safe_callback_answer(callback, err[:200] or "Ошибка", show_alert=True)
                return
            await safe_callback_answer(callback)
            return
        if data.startswith("help:"):
            page = data.split(":", 1)[1].strip() or "main"
            is_admin = layer._admin_module.is_admin(uid)
            chunks, kb = build_help_payload(plugin_registry=layer.plugin_registry, is_admin=is_admin, page=page)
            if not callback.message:
                await safe_callback_answer(callback, "Сообщение недоступно — отправьте /help снова", show_alert=True)
                return
            await safe_callback_answer(callback)
            await _send_help_pages(callback.message, chunks, kb, bot=callback.bot)
            return
        if data.startswith("aus:"):
            if not layer._admin_module.is_admin(uid):
                await safe_callback_answer(callback, "Нет доступа", show_alert=True)
                return
            tid = (data.split(":", 1)[1] if ":" in data else "").strip().lower()
            res = toggle_by_id(tid)
            if not res:
                await safe_callback_answer(callback, "Неизвестный переключатель", show_alert=True)
                return
            _ek, new_val = res
            text = sanitize_html(layer._admin_module.settings_panel_html()[:4090])
            kb = layer._admin_module.settings_keyboard()
            await safe_callback_answer(callback, "Вкл" if new_val else "Выкл")
            try:
                await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
            except Exception:
                await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
            return
        if not data.startswith("admin:"):
            await safe_callback_answer(callback)
            return
        if not layer._admin_module.is_admin(uid):
            await safe_callback_answer(callback, "Нет доступа", show_alert=True)
            return
        from core.input_handlers.help_payload import admin_stats_button_rows
        from core.input_handlers.telegram_nav import admin_detail_footer_rows, merge_keyboards

        key = data.split(":", 1)[1]
        if key.startswith("menu_"):
            try:
                menu_page = max(1, int(key.split("_", 1)[1]))
            except (ValueError, IndexError):
                menu_page = 1
            await safe_callback_answer(callback)
            text = sanitize_html(layer._admin_module.menu_hub_html(page=menu_page)[:4090])
            reply_markup = layer._admin_module.menu_keyboard(page=menu_page)
            try:
                await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
            except Exception:
                await callback.message.answer(text, reply_markup=reply_markup, parse_mode="HTML")
            return
        if key == "seed_menu":
            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="Заполнить пустые", callback_data="admseed:miss"),
                        InlineKeyboardButton(text="Директива force", callback_data="admseed:dir"),
                    ],
                    [
                        InlineKeyboardButton(text="Всё из примеров", callback_data="admseed:all"),
                    ],
                    [
                        InlineKeyboardButton(text="◀️ Панель", callback_data="admin:dashboard"),
                    ],
                ]
            )
            await safe_callback_answer(callback)
            text = sanitize_html(layer._admin_module.callback_body_html("seed_menu")[:4090])
            try:
                await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
            except Exception:
                await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
            return
        await safe_callback_answer(callback)
        if key == "dashboard":
            text = sanitize_html(layer._admin_module.dashboard_html()[:4090])
            reply_markup = layer._admin_module.menu_keyboard(page=1)
        elif key == "run_learning":
            import json

            from core.learning_maintenance import maybe_run_learning_maintenance

            rep = maybe_run_learning_maintenance(force=True)
            body = sanitize_html(json.dumps(rep, ensure_ascii=False, indent=2)[:3900])
            try:
                await callback.message.answer(f"<pre>{body}</pre>", parse_mode="HTML")
            except Exception:
                await callback.message.answer("Обучение выполнено (см. логи).")
            return
        else:
            text = sanitize_html(layer._admin_module.callback_body_html(key)[:4090])
            footer = admin_detail_footer_rows()
            if key == "settings":
                reply_markup = merge_keyboards(
                    layer._admin_module.settings_keyboard(), footer
                )
            elif key == "commands":
                reply_markup = layer._admin_module.commands_quick_keyboard()
            elif key == "stats":
                reply_markup = merge_keyboards(
                    InlineKeyboardMarkup(inline_keyboard=admin_stats_button_rows()),
                    footer,
                )
            elif key == "reputation":
                reply_markup = merge_keyboards(
                    InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(text="⭐ Репутация", callback_data="hs:0"),
                                InlineKeyboardButton(text="📄 JSON", callback_data="hs:rep_json"),
                            ],
                        ]
                    ),
                    footer,
                )
            elif key == "learning":
                reply_markup = merge_keyboards(
                    InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(text="🧠 Дайджест", callback_data="hs:1"),
                                InlineKeyboardButton(text="⚠️ Кластеры", callback_data="hs:2"),
                            ],
                            [
                                InlineKeyboardButton(
                                    text="▶️ Обучение сейчас",
                                    callback_data="admin:run_learning",
                                ),
                            ],
                        ]
                    ),
                    footer,
                )
            else:
                reply_markup = merge_keyboards(None, footer)
        try:
            await callback.message.edit_text(
                text,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
        except Exception:
            await callback.message.answer(
                text,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
