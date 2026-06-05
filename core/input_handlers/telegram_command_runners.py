"""
Тела slash-команд, общие для aiogram Command(...) и для inline-кнопок.

Inline-кнопки вызывают _process_message с synthetic_payload: message.text остаётся
старым (HTML справки), поэтому Command-фильтры не срабатывают — нужен второй вход
с тем же кодом ответа.
"""
from __future__ import annotations

import logging

import asyncio
import os
import re
import tempfile
from typing import Any, Optional
from urllib.parse import urlparse

from aiogram.enums import ChatType
from aiogram.types import FSInputFile, KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove, ReplyParameters

from core.input_handlers.help_payload import build_help_payload
from core.telegram_ui import (
    esc,
    format_corpus_catalog_html,
    format_facts_html,
    format_facts_refresh_html,
    format_me_html,
    format_mem0_facts_html,
    format_plugins_help_html,
    format_plugins_status_html,
    format_psych_html,
    format_system_state_html,
    format_twin_html,
)
from core.telegram_util import answer_with_retry, reply_html_chunks, sanitize_html
from core.input_handlers.admin_access import admin_guard
from core.ephemeral_autolearn import pending_clear_all_pending, pending_list
from core.ephemeral_lessons import (
    add_lesson,
    deactivate_all_lessons,
    deactivate_lesson,
    export_for_cursor,
    load_document,
    parse_remember_patch,
)
from core.telegram_util import reply_code_plain_chunks, reply_json_chunks


logger = logging.getLogger(__name__)

def _profile_group_id(message: Message) -> Optional[str]:
    """Контекст сессии поведения: в ЛС — агрегат всех файлов; в группе — этот чат."""
    if getattr(message.chat, "type", None) == ChatType.PRIVATE:
        return None
    return str(message.chat.id)


def _note_private_seen_after_slash_command(layer: Any, message: Message) -> None:
    """Slash обработан отдельным Command-хендлером (не через pipeline) — не дублировать private_intro на первом обычном сообщении."""
    if getattr(message.chat, "type", None) != "private" or message.from_user is None:
        return
    try:
        layer.note_private_user_seen_for_intro(str(message.from_user.id))
    except Exception as e:
        logger.debug('%s optional failed: %s', 'telegram_command_runners', e, exc_info=True)
async def run_start(_layer: Any, message: Message) -> None:
    _note_private_seen_after_slash_command(_layer, message)
    await answer_with_retry(
        message,
        "Привет! Я универсальный ассистент. Пиши что угодно.",
        reply_markup=ReplyKeyboardRemove(),
    )


async def run_help(layer: Any, message: Message) -> None:
    _note_private_seen_after_slash_command(layer, message)
    is_admin = layer._admin_module.is_admin(str(message.from_user.id))
    chunks, kb = build_help_payload(plugin_registry=layer.plugin_registry, is_admin=is_admin, page="main")
    for i, chunk in enumerate(chunks):
        if i == 0 and kb is not None:
            await answer_with_retry(message, chunk, reply_markup=kb, parse_mode="HTML")
        else:
            await answer_with_retry(message, chunk, parse_mode="HTML")


async def run_geo_help(_layer: Any, message: Message) -> None:
    """Памятка по картам + reply-клавиатура с запросом геолокации (Telegram)."""
    _note_private_seen_after_slash_command(_layer, message)
    chat_t = getattr(message.chat, "type", None)
    is_group = chat_t in (ChatType.GROUP, ChatType.SUPERGROUP)
    # В личке selective=True часто ломает показ клавиатуры; в группах без selective+reply её не видно.
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📍 Поделиться геолокацией", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
        selective=bool(is_group),
    )
    reply_extras: dict[str, Any] = {}
    if is_group:
        try:
            reply_extras["reply_parameters"] = ReplyParameters(message_id=int(message.message_id))
        except (TypeError, ValueError):
            pass
    text = (
        "🗺 <b>Карты и геолокация</b>\n\n"
        "<blockquote>"
        "Нажмите кнопку <b>над буквенной клавиатурой</b> (это не inline-кнопки в сообщении). "
        "В приложении Telegram она в ряду с клавиатурой бота; иногда нужно нажать иконку "
        "клавиатуры у поля ввода. В <b>Telegram Web</b> reply-клавиатура часто не показывается — "
        "лучше приложение на телефоне или десктоп.\n\n"
        + (
            "В <b>группе</b> эта клавиатура обычно только у вас; дальше локацию при необходимости шлите "
            "<b>ответом на сообщение бота</b> или при активном режиме чата.\n\n"
            if is_group
            else ""
        )
        + "После нажатия откроется выбор точки — отправьте её, бот получит координаты (GeoMaps, погода, «рядом», маршрут).\n\n"
        "Можно и текстом: «маршрут до…», «кафе рядом»."
        "</blockquote>\n\n"
        "<i>Убрать клавиатуру: <code>/start</code> или после отправки точки (one-time).</i>"
    )
    await answer_with_retry(message, text, reply_markup=kb, parse_mode="HTML", **reply_extras)


async def run_plugins(layer: Any, message: Message) -> None:
    _note_private_seen_after_slash_command(layer, message)
    reg = getattr(layer, "plugin_registry", None)
    if reg is None:
        await answer_with_retry(message, "Реестр плагинов недоступен.")
        return
    items: list[dict] = []
    loaded = getattr(reg, "loaded_modules", {}) or {}
    modules = getattr(reg, "modules", {}) or {}
    for name in sorted(modules.keys()):
        mi = modules.get(name)
        if mi is None:
            continue
        manifest = getattr(mi, "manifest", None)
        st_obj = getattr(mi, "state", None)
        st = getattr(st_obj, "status", "disabled") if st_obj is not None else "disabled"
        err = getattr(st_obj, "last_error", None) if st_obj is not None else None
        ver = getattr(manifest, "version", None) if manifest is not None else None
        typ = getattr(manifest, "type", "") if manifest is not None else ""
        items.append(
            {
                "name": name,
                "type": typ,
                "version": ver,
                "loaded": name in loaded,
                "status": st,
                "error": err or "",
            }
        )
    await reply_html_chunks(message, format_plugins_status_html(items))


async def run_plugins_help(_layer: Any, message: Message) -> None:
    _note_private_seen_after_slash_command(_layer, message)
    await reply_html_chunks(message, format_plugins_help_html())


async def run_system_state(layer: Any, message: Message) -> None:
    try:
        info = layer.orchestrator.get_system_info()
        try:
            auto_on = (os.getenv("BRAIN_AUTO_REASONING_PLUGINS", "true").strip().lower() in {"1", "true", "yes", "on"})
            try:
                auto_timeout = max(0.3, float((os.getenv("BRAIN_AUTO_REASONING_TIMEOUT_SEC") or "1.8").strip()))
            except ValueError:
                auto_timeout = 1.8
            mon = info.get("monitoring") if isinstance(info.get("monitoring"), dict) else {}
            ctr = mon.get("counters") if isinstance(mon.get("counters"), dict) else {}
            runs = int(ctr.get("auto_reasoning_runs_total", 0) or 0)
            plugins_total = int(ctr.get("auto_reasoning_plugins_total", 0) or 0)
            routed_total = int(ctr.get("auto_reasoning_routed_total", 0) or 0)
            local_calls_total = int(ctr.get("auto_reasoning_local_calls_total", 0) or 0)
            saved_tokens_total = int(ctr.get("auto_reasoning_est_saved_tokens_total", 0) or 0)
            baseline_tokens_total = int(ctr.get("auto_reasoning_est_baseline_tokens_total", 0) or 0)
            efficiency_pct = round((saved_tokens_total / baseline_tokens_total) * 100.0, 2) if baseline_tokens_total > 0 else None
            info = dict(info)
            info["auto_reasoning"] = {
                "enabled": auto_on,
                "mode": "routed",
                "plugins_count": 25,
                "avg_plugins_per_task": round((plugins_total / runs), 2) if runs > 0 else None,
                "avg_routed_per_task": round((routed_total / runs), 2) if runs > 0 else None,
                "avg_local_calls_per_task": round((local_calls_total / runs), 2) if runs > 0 else None,
                "timeout_sec": auto_timeout,
                "token_efficiency_percent": efficiency_pct,
                "estimated_saved_tokens_total": saved_tokens_total,
                "estimated_baseline_tokens_total": baseline_tokens_total,
            }
        except Exception as e:
            logger.debug('%s optional failed: %s', 'telegram_command_runners', e, exc_info=True)
        try:
            from core.reasoning_status import load_reasoning_bench_snapshot

            rs = load_reasoning_bench_snapshot()
            if rs:
                info = dict(info)
                info["reasoning_snapshot"] = rs
        except Exception as e:
            logger.debug('%s optional failed: %s', 'telegram_command_runners', e, exc_info=True)
        uid = str(message.from_user.id) if message.from_user else ""
        if uid:
            snap: dict = {"knowledge_archive_entries": 0, "mem0_facts": None}
            try:
                from core.user_knowledge_archive_module import count_archive_entries_for_user

                snap["knowledge_archive_entries"] = int(count_archive_entries_for_user(uid))
            except Exception as e:
                logger.debug('%s optional failed: %s', 'telegram_command_runners', e, exc_info=True)
            mm = layer.mem0_memory
            if mm is not None:
                try:
                    facts = await asyncio.to_thread(mm.get_facts, uid)
                    snap["mem0_facts"] = len(facts) if isinstance(facts, list) else 0
                except Exception:
                    snap["mem0_facts"] = None
            info = dict(info)
            info["user_memory_snapshot"] = snap
        await answer_with_retry(message, format_system_state_html(info), parse_mode="HTML")
    except Exception as e:
        await answer_with_retry(message, f"Ошибка: {e}")


async def run_get_mem0_facts(layer: Any, message: Message) -> None:
    _note_private_seen_after_slash_command(layer, message)
    try:
        user_id = str(message.from_user.id)
        if not layer.mem0_memory:
            await answer_with_retry(message, "Mem0 отключён.")
            return
        facts = layer.mem0_memory.get_facts(user_id)
        await reply_html_chunks(message, format_mem0_facts_html(facts))
    except Exception as e:
        await answer_with_retry(message, f"Ошибка: {e}")


async def run_id(_layer: Any, message: Message) -> None:
    _note_private_seen_after_slash_command(_layer, message)
    uid = message.from_user.id
    un = (message.from_user.username or "").strip()
    lines = [f"Ваш Telegram ID: <code>{uid}</code>"]
    if un:
        lines.append(f"Username: @{un}")
    await answer_with_retry(message, "\n".join(lines), parse_mode="HTML")


async def run_me(layer: Any, message: Message) -> None:
    _note_private_seen_after_slash_command(layer, message)
    uid = str(message.from_user.id)
    summary = layer._user_mgmt.me_summary(uid, _profile_group_id(message))
    orch = getattr(layer, "orchestrator", None)
    if orch is not None:
        pe = getattr(orch, "psychology_engine", None)
        if pe and hasattr(pe, "get_psychology_profile"):
            summary["psychology"] = pe.get_psychology_profile(uid) or {}
        dt = getattr(orch, "digital_twin", None)
        if dt and hasattr(dt, "get_digital_twin"):
            summary["digital_twin"] = dt.get_digital_twin(uid) or {}
    await reply_html_chunks(message, format_me_html(summary))


async def run_psych(layer: Any, message: Message) -> None:
    _note_private_seen_after_slash_command(layer, message)
    uid = str(message.from_user.id)
    prof: dict = {}
    orch = getattr(layer, "orchestrator", None)
    if orch is not None:
        pe = getattr(orch, "psychology_engine", None)
        if pe and hasattr(pe, "get_psychology_profile"):
            prof = pe.get_psychology_profile(uid) or {}
    await reply_html_chunks(message, format_psych_html(prof))


async def run_twin(layer: Any, message: Message) -> None:
    _note_private_seen_after_slash_command(layer, message)
    uid = str(message.from_user.id)
    twin: dict = {}
    orch = getattr(layer, "orchestrator", None)
    if orch is not None:
        dt = getattr(orch, "digital_twin", None)
        if dt and hasattr(dt, "get_digital_twin"):
            twin = dt.get_digital_twin(uid) or {}
    await reply_html_chunks(message, format_twin_html(twin))


async def run_chat_style(layer: Any, message: Message) -> None:
    _note_private_seen_after_slash_command(layer, message)
    from core.input_handlers.conversation_style_telegram import run_chat_style as _panel

    await _panel(layer, message)


async def run_facts(layer: Any, message: Message) -> None:
    _note_private_seen_after_slash_command(layer, message)
    uid = str(message.from_user.id)
    await reply_html_chunks(
        message, format_facts_html(layer._user_mgmt.facts_summary(uid, _profile_group_id(message)))
    )


async def run_rate(layer: Any, message: Message) -> None:
    _note_private_seen_after_slash_command(layer, message)
    from core.telegram_util import sanitize_html
    from core.user_response_feedback import apply_user_rating, parse_rate_args

    uid = str(message.from_user.id)
    parts = (message.text or "").split(maxsplit=1)
    rest = parts[1] if len(parts) > 1 else ""
    score, note = parse_rate_args(rest)
    if score is None:
        await message.answer(
            sanitize_html(
                "Использование: <code>/rate +1</code> или <code>/rate -1</code> [замечание]\n"
                "Оценивает <b>последний ответ</b> бота (маршрут из session_task)."
            ),
            parse_mode="HTML",
        )
        return
    bs = getattr(layer, "behavior_store", None) or getattr(layer, "_behavior_store", None)
    rep = apply_user_rating(
        user_id=uid,
        score=score,
        behavior_store=bs,
        correction_text=note,
        source="rate",
    )
    from core.user_correction_bus import format_learning_ack_from_rating

    ack = format_learning_ack_from_rating(rep) if score < 0 else ""
    applied = ", ".join(rep.get("applied") or []) or "—"
    body = ack or f"Оценка <b>{score:+d}</b> учтена.\nПрименено: <code>{applied}</code>"
    if ack and score < 0:
        body = f"{ack}\n\n<i>Технически:</i> <code>{applied}</code>"
    elif score > 0:
        body = f"Оценка <b>+1</b> учтена. Спасибо!"
    await message.answer(sanitize_html(body), parse_mode="HTML")


async def run_correct(layer: Any, message: Message) -> None:
    _note_private_seen_after_slash_command(layer, message)
    from core.telegram_util import sanitize_html
    from core.user_response_feedback import apply_user_rating

    uid = str(message.from_user.id)
    reply = message.reply_to_message
    correction = ""
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) > 1:
        correction = parts[1].strip()
    if reply and not correction:
        correction = (reply.text or reply.caption or "").strip()[:400]
    if not correction:
        await message.answer(
            sanitize_html(
                "Использование: ответьте <b>реплаем</b> на сообщение бота с текстом поправки "
                "или <code>/correct ваш вариант</code>"
            ),
            parse_mode="HTML",
        )
        return
    bs = getattr(layer, "behavior_store", None) or getattr(layer, "_behavior_store", None)
    rep = apply_user_rating(
        user_id=uid,
        score=-1,
        behavior_store=bs,
        correction_text=correction,
        source="correct",
    )
    from core.user_correction_bus import format_learning_ack_from_rating

    ack = format_learning_ack_from_rating(rep)
    applied = ", ".join(rep.get("applied") or []) or "—"
    body = ack or f"Поправка сохранена.\nПрименено: <code>{applied}</code>"
    if ack:
        body = f"{ack}\n\n<i>Технически:</i> <code>{applied}</code>"
    await message.answer(sanitize_html(body), parse_mode="HTML")


async def run_forget(layer: Any, message: Message) -> None:
    _note_private_seen_after_slash_command(layer, message)
    uid = str(message.from_user.id)
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /forget <field>")
        return
    ok = layer._user_mgmt.forget_field(uid, parts[1].strip(), _profile_group_id(message))
    await message.answer("Готово." if ok else "Поле не найдено.")


async def run_facts_refresh(layer: Any, message: Message) -> None:
    _note_private_seen_after_slash_command(layer, message)
    uid = str(message.from_user.id)
    await reply_html_chunks(
        message, format_facts_refresh_html(layer._user_mgmt.facts_refresh(uid, _profile_group_id(message)))
    )


async def run_facts_reset(layer: Any, message: Message) -> None:
    _note_private_seen_after_slash_command(layer, message)
    uid = str(message.from_user.id)
    layer._user_mgmt.facts_reset(uid, _profile_group_id(message))
    await message.answer("Факты сброшены.")


async def run_new_conversation(layer: Any, message: Message) -> None:
    """/new — новый conversation_epoch, очистка recent, новый KV session."""
    _note_private_seen_after_slash_command(layer, message)
    uid = str(message.from_user.id)
    gid = _profile_group_id(message)
    bs = getattr(getattr(layer, "orchestrator", None), "behavior_store", None)
    if bs is None:
        await message.answer("Хранилище диалога недоступно.")
        return
    from core.conversation_epoch import start_new_conversation

    new_id, _rec = start_new_conversation(bs, uid, gid, reason="slash_new")
    await message.answer(
        f"Новый диалог (эпоха {new_id}). Краткий контекст сброшен; факты профиля сохранены."
    )


# --- Латки (админ) ---


async def run_remember_patch(layer: Any, message: Message) -> None:
    _note_private_seen_after_slash_command(layer, message)
    if not await admin_guard(message, layer):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(
            sanitize_html(
                "Формат: <code>/remember_patch триггер || инструкция [ || force_general ]</code>\n"
                "Триггер с префиксом <code>regex:</code> — совпадение по regex.\n"
                "Третья часть <code>force_general</code> — при математическом эвристическом intent вести как general "
                "(как для t.me/+ ссылок).\n"
                "Пример: <code>/remember_patch t.me/+ || не предлагай /calc, это приглашение || force_general</code>",
            ),
            parse_mode="HTML",
        )
        return
    try:
        trig, inst, is_rx, fg = parse_remember_patch(parts[1])
        row = add_lesson(
            trig,
            inst,
            match_regex=is_rx,
            force_general_when_math_probe=fg,
        )
    except ValueError as e:
        await message.answer(f"Ошибка: {e}")
        return
    await message.answer(
        sanitize_html(f"Запомнено <code>{row.get('id')}</code> (срабатываний по дедупу: {row.get('hit_count')})."),
        parse_mode="HTML",
    )


async def run_clear_all_patches(layer: Any, message: Message) -> None:
    """Отключить все латки; опционально очистить очередь pending."""
    _note_private_seen_after_slash_command(layer, message)
    if not await admin_guard(message, layer):
        return
    parts = (message.text or "").split(maxsplit=1)
    mode = parts[1].strip().lower() if len(parts) > 1 else ""
    n_lessons = 0
    n_pending = 0
    if mode in ("queue", "очередь"):
        n_pending = pending_clear_all_pending()
    elif mode in ("full", "all", "всё", "все"):
        n_lessons = deactivate_all_lessons()
        n_pending = pending_clear_all_pending()
    else:
        n_lessons = deactivate_all_lessons()
    await message.answer(
        sanitize_html(
            f"Готово: отключено латок: <b>{n_lessons}</b>, снято из очереди pending: <b>{n_pending}</b>.\n"
            "<i>Записи в ephemeral_lessons.json остаются с active=false. "
            "Полностью стереть файл можно вручную (бот выключен).</i>"
        ),
        parse_mode="HTML",
    )


async def run_forget_patch(layer: Any, message: Message) -> None:
    _note_private_seen_after_slash_command(layer, message)
    if not await admin_guard(message, layer):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(sanitize_html("Использование: /forget_patch &lt;id&gt;"), parse_mode="HTML")
        return
    lid = parts[1].strip()
    if deactivate_lesson(lid):
        await message.answer(sanitize_html(f"Отключено: <code>{esc(lid)}</code>"), parse_mode="HTML")
    else:
        await message.answer("id не найден или уже отключён.")


async def run_export_patches(layer: Any, message: Message) -> None:
    _note_private_seen_after_slash_command(layer, message)
    if not await admin_guard(message, layer):
        return
    bundle = export_for_cursor()
    md = bundle.get("markdown_for_cursor") or ""
    await reply_code_plain_chunks(message, md)
    payload = {k: v for k, v in bundle.items() if k != "markdown_for_cursor"}
    await reply_json_chunks(message, payload, ensure_ascii=False, indent=2)


async def run_list_patches(layer: Any, message: Message) -> None:
    _note_private_seen_after_slash_command(layer, message)
    if not await admin_guard(message, layer):
        return
    doc = load_document()
    rows = [
        x
        for x in (doc.get("lessons") or [])
        if isinstance(x, dict) and x.get("active", True)
    ]
    if not rows:
        await message.answer("Активных латок нет.")
        return
    lines = []
    for le in sorted(rows, key=lambda x: float(x.get("created_ts") or 0.0), reverse=True):
        t = str(le.get("trigger", ""))[:60]
        lines.append(
            f"<code>{esc(str(le.get('id')))}</code> {esc(le.get('match', ''))} "
            f"hits={le.get('hit_count', 0)} <i>{esc(t)}</i>"
        )
    await message.answer(sanitize_html("\n".join(lines)), parse_mode="HTML")


async def run_pending_suggested_patch(layer: Any, message: Message) -> None:
    _note_private_seen_after_slash_command(layer, message)
    if not await admin_guard(message, layer):
        return
    rows = pending_list()
    if not rows:
        await message.answer("Очередь предложений пуста.")
        return
    lines = []
    for it in rows[:15]:
        ins = str(it.get("instruction") or "")[:120]
        distinct = len(it.get("supporter_user_ids") or []) or int(it.get("supporters") or 0)
        lines.append(
            f"<code>{esc(str(it.get('id')))}</code> from=<code>{esc(str(it.get('from_user_id')))}</code> "
            f"distinct={distinct} fg={it.get('force_general_when_math_probe')}\n<i>{esc(ins)}</i>"
        )
    lines.append("\n/approve_suggested_patch &lt;id&gt; или /dismiss_suggested_patch &lt;id&gt;")
    await message.answer(sanitize_html("\n\n".join(lines)), parse_mode="HTML")


async def run_filefrom(_layer: Any, message: Message) -> None:
    """Скачать публичный URL и отправить в чат как документ (обход «пришли файлом» без правки кода)."""
    _note_private_seen_after_slash_command(_layer, message)
    raw = (message.text or "").strip()
    parts = raw.split(maxsplit=1)
    if len(parts) < 2:
        await answer_with_retry(
            message,
            "Укажите ссылку: /filefrom https://example.com/file.pdf\n"
            "Используются те же ограничения безопасности, что и у UrlFetch (без локальных адресов).",
        )
        return
    url = parts[1].strip().split()[0]
    from core.url_fetch import _validate_http_url, safe_fetch_raw

    ok, err = _validate_http_url(url)
    if not ok:
        await answer_with_retry(message, f"Ссылка не подходит: {err}")
        return
    await message.bot.send_chat_action(message.chat.id, "upload_document")
    fr = await safe_fetch_raw(url)
    if fr.get("error"):
        await answer_with_retry(message, f"Не удалось скачать: {fr.get('error')}")
        return
    body = fr.get("raw") or b""
    if not body:
        await answer_with_retry(message, "Сервер вернул пустое тело.")
        return
    try:
        max_b = int((os.getenv("FILEFROM_MAX_BYTES") or str(45 * 1024 * 1024)).strip())
    except ValueError:
        max_b = 45 * 1024 * 1024
    if len(body) > max_b:
        await answer_with_retry(
            message,
            f"Файл слишком большой ({len(body)} байт). Лимит {max_b} (FILEFROM_MAX_BYTES).",
        )
        return
    path_part = urlparse(url).path or ""
    base = os.path.basename(path_part) or "download"
    base = re.sub(r"[^\w.\-]+", "_", base).strip("._") or "download"
    if "." not in base:
        ct = (fr.get("content_type") or "").lower()
        if "pdf" in ct:
            base += ".pdf"
        elif "html" in ct:
            base += ".html"
        elif "json" in ct:
            base += ".json"
        elif "text/plain" in ct:
            base += ".txt"
    fd, tmp = tempfile.mkstemp(prefix="gemma_filefrom_", suffix="_" + base[-80:])
    os.close(fd)
    try:
        with open(tmp, "wb") as f:
            f.write(body)
        cap = url if len(url) <= 900 else url[:897] + "…"
        await message.answer_document(
            FSInputFile(tmp, filename=base[-240:]),
            caption=cap,
        )
    except Exception as e:
        await answer_with_retry(message, f"Не удалось отправить файл: {e}")
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


async def run_corpus_doc(_layer: Any, message: Message) -> None:
    """Отправить локальный оригинал из DocumentCorpus (книга .txt или акт .json)."""
    _note_private_seen_after_slash_command(_layer, message)
    raw = (message.text or "").strip()
    parts = raw.split(maxsplit=1)
    if len(parts) < 2:
        await answer_with_retry(
            message,
            "Укажите id: <code>/corpus_doc book:…</code> или <code>law:…</code> "
            "(списки: <code>/corpus_books</code>, <code>/corpus_docs</code>).",
            parse_mode="HTML",
        )
        return
    doc_id = parts[1].strip().split()[0]
    from core.document_corpus_store import corpus_enabled, get_original_for_telegram

    if not corpus_enabled():
        await answer_with_retry(message, "Корпус документов выключен (DOCUMENT_CORPUS_ENABLED).")
        return
    r = get_original_for_telegram(doc_id)
    if not r.get("ok"):
        await answer_with_retry(message, f"Не удалось: {r.get('error')}")
        return
    path = str(r.get("path") or "")
    try:
        sz = os.path.getsize(path)
    except OSError:
        await answer_with_retry(message, "Файл недоступен.")
        return
    try:
        max_b = int((os.getenv("FILEFROM_MAX_BYTES") or str(45 * 1024 * 1024)).strip())
    except ValueError:
        max_b = 45 * 1024 * 1024
    if sz > max_b:
        await answer_with_retry(
            message,
            f"Файл слишком большой ({sz} байт). Лимит {max_b} (FILEFROM_MAX_BYTES).",
        )
        return
    await message.bot.send_chat_action(message.chat.id, "upload_document")
    fn = r.get("filename") or os.path.basename(path)
    try:
        cap = sanitize_html(f"{r.get('kind') or ''} · <code>{esc(doc_id)}</code>")
        await message.answer_document(
            FSInputFile(path, filename=str(fn)[-240:]),
            caption=cap,
            parse_mode="HTML",
        )
    except Exception as e:
        await answer_with_retry(message, f"Не удалось отправить файл: {e}")


def _corpus_catalog_offset(message: Message) -> int:
    raw = (message.text or "").strip().split()
    if len(raw) < 2:
        return 0
    try:
        return max(0, int(raw[1]))
    except ValueError:
        return 0


_CORPUS_LIST_PAGE = 80


async def run_corpus_books(_layer: Any, message: Message) -> None:
    """Список книг в DocumentCorpus (пагинация: /corpus_books 80)."""
    _note_private_seen_after_slash_command(_layer, message)
    from core.document_corpus_store import corpus_catalog, corpus_enabled

    if not corpus_enabled():
        await answer_with_retry(message, "Корпус документов выключен (DOCUMENT_CORPUS_ENABLED).")
        return
    off = _corpus_catalog_offset(message)
    data = corpus_catalog(mode="books", limit=_CORPUS_LIST_PAGE, offset=off)
    await reply_html_chunks(message, format_corpus_catalog_html(data))


async def run_corpus_docs(_layer: Any, message: Message) -> None:
    """Список документов (не книг): НПА, shared_ingest и т.д."""
    _note_private_seen_after_slash_command(_layer, message)
    from core.document_corpus_store import corpus_catalog, corpus_enabled

    if not corpus_enabled():
        await answer_with_retry(message, "Корпус документов выключен (DOCUMENT_CORPUS_ENABLED).")
        return
    off = _corpus_catalog_offset(message)
    data = corpus_catalog(mode="documents", limit=_CORPUS_LIST_PAGE, offset=off)
    await reply_html_chunks(message, format_corpus_catalog_html(data))


async def run_corpus_file(_layer: Any, message: Message) -> None:
    """Отправить оригинальный файл из corpus_files_dir по document_id."""
    _note_private_seen_after_slash_command(_layer, message)
    raw = (message.text or "").strip()
    parts = raw.split(maxsplit=1)
    if len(parts) < 2:
        await answer_with_retry(
            message,
            "Укажите id: <code>/corpus_file file:…</code> "
            "(списки: <code>/corpus_books</code>, <code>/corpus_docs</code>).",
            parse_mode="HTML",
        )
        return
    doc_id = parts[1].strip().split()[0]
    from core.document_corpus_store import corpus_enabled, get_path_for_corpus_file

    if not corpus_enabled():
        await answer_with_retry(message, "Корпус документов выключен (DOCUMENT_CORPUS_ENABLED).")
        return
    path = get_path_for_corpus_file(doc_id)
    if not path:
        await answer_with_retry(message, f"Файл не найден для {esc(doc_id)}.", parse_mode="HTML")
        return
    try:
        sz = os.path.getsize(path)
    except OSError:
        await answer_with_retry(message, "Файл недоступен.")
        return
    try:
        max_b = int((os.getenv("FILEFROM_MAX_BYTES") or str(45 * 1024 * 1024)).strip())
    except ValueError:
        max_b = 45 * 1024 * 1024
    if sz > max_b:
        await answer_with_retry(
            message,
            f"Файл слишком большой ({sz} байт). Лимит {max_b} (FILEFROM_MAX_BYTES).",
        )
        return
    await message.bot.send_chat_action(message.chat.id, "upload_document")
    fn = os.path.basename(path)
    try:
        cap = sanitize_html(f"user_file · <code>{esc(doc_id)}</code>")
        await message.answer_document(
            FSInputFile(path, filename=str(fn)[-240:]),
            caption=cap,
            parse_mode="HTML",
        )
    except Exception as e:
        await answer_with_retry(message, f"Не удалось отправить файл: {e}")


async def run_corpus_delete(_layer: Any, message: Message) -> None:
    """Удалить документ из корпуса: FTS, метаданные и оригинальный файл."""
    _note_private_seen_after_slash_command(_layer, message)
    raw = (message.text or "").strip()
    parts = raw.split(maxsplit=1)
    if len(parts) < 2:
        await answer_with_retry(
            message,
            "Укажите id: <code>/corpus_delete file:…</code>",
            parse_mode="HTML",
        )
        return
    doc_id = parts[1].strip().split()[0]
    from core.document_corpus_store import corpus_enabled, delete_document_from_corpus

    if not corpus_enabled():
        await answer_with_retry(message, "Корпус документов выключен (DOCUMENT_CORPUS_ENABLED).")
        return
    r = delete_document_from_corpus(doc_id)
    if not r.get("ok"):
        await answer_with_retry(message, f"Не удалось: {r.get('error')}")
        return
    file_msg = " + файл" if r.get("file_deleted") else ""
    await answer_with_retry(
        message,
        f"Удалено: <code>{esc(doc_id)}</code>{file_msg}.",
        parse_mode="HTML",
    )


async def run_note(_layer: Any, message: Message) -> None:
    """Сохранить пользовательскую заметку."""
    _note_private_seen_after_slash_command(_layer, message)
    raw = (message.text or "").strip()
    parts = raw.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await answer_with_retry(
            message,
            "Напиши так: /note запомни, что я люблю зелёный чай",
        )
        return
    text = parts[1].strip()
    uid = str(message.from_user.id) if message.from_user else "unknown"
    entry = {
        "ts": __import__("time").time(),
        "user_id": uid,
        "text": text,
    }
    try:
        notes_dir = os.path.join(os.getenv("RESILIENCE_RUNTIME_DIR", "data/runtime"))
        os.makedirs(notes_dir, exist_ok=True)
        path = os.path.join(notes_dir, "user_notes.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(__import__("json").dumps(entry, ensure_ascii=False) + "\n")
            f.flush()
        await answer_with_retry(message, f"✅ Запомнил: {text[:200]}")
    except OSError as e:
        await answer_with_retry(message, f"Не удалось сохранить: {e}")
