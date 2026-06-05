"""Общая сборка ZIP багрепорта: /admin_bug и NL-триггер «зафиксируй баг»."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from aiogram.types import BufferedInputFile, Message

from core.admin_bug_report import (
    build_bug_report_document,
    bug_nl_args_remainder,
    parse_admin_bug_command_args,
    prose_wants_bug_report_capture,
)
from core.admin_zip_copy import copy_admin_zip_to_data_tools
from core.diagnostic_bundle import admin_bug_report_zip_bytes, build_diagnostic_bundle
from core.bug_report_user import (
    bug_report_forward_recipient_ids,
    bug_report_user_submit_enabled,
    sanitize_user_bug_args,
    user_bug_cooldown_ok,
)
from core.monitoring import MONITOR
from core.telegram_ui import esc
from core.telegram_util import reply_html_chunks, sanitize_html

logger = logging.getLogger(__name__)

ZipDelivery = Literal["to_sender", "to_admins_only"]

_BUG_HELP_ARGS = frozenset({"help", "h", "?", "-h", "--help", "пример", "примеры"})

_USER_BUG_HELP_HTML = "\n".join(
    [
        "<b>🐞 Сообщить о проблеме</b>",
        "",
        "1) В <b>личке с ботом</b> ответьте <b>реплаем</b> на сообщение бота, где ошибка.",
        "2) Отправьте <code>/bug</code> или напишите в начале строки: <code>Зафиксируй баг</code>",
        "",
        "Технический архив уходит <b>только разработчику</b>; вам придёт короткое подтверждение.",
        "Опционально в хвосте: число строк лога (например <code>40</code>), <code>comp=voice</code>, короткая заметка.",
    ]
)


def _reporter_banner_html(message: Message) -> str:
    u = message.from_user
    if u is None:
        return "📩 <b>Багрепорт от пользователя</b>\n<i>user unknown</i>"
    bits = []
    if getattr(u, "username", None):
        bits.append(f"@{esc(u.username)}")
    bits.append(f"id <code>{u.id}</code>")
    fn = " ".join(
        p for p in ((u.first_name or ""), (u.last_name or "")) if p
    ).strip()
    if fn:
        bits.append(esc(fn))
    return "📩 <b>Багрепорт от пользователя</b>\n" + " · ".join(bits)


def _tail_n() -> int:
    try:
        return max(1, min(int((os.getenv("BUG_CAPTURE_RECENT_MESSAGES") or "5").strip()), 20))
    except ValueError:
        return 5


async def run_admin_bug_flow(
    layer: Any,
    message: Message,
    *,
    command_args: Optional[str] = None,
    recent_chat_tail: Optional[List[Dict[str, Any]]] = None,
    capture_source: str = "slash_command",
    zip_delivery: ZipDelivery = "to_sender",
) -> None:
    """
    Собрать и отправить архив багрепорта (как handle_admin_bug).
    zip_delivery=to_admins_only — ZIP только получателям из .env; пользователю без вложения.
    """
    try:
        from core.telegram_recent_messages import record_incoming_message, recent_tail_for_chat

        rec_txt = (message.text or message.caption or "").strip()
        if rec_txt:
            record_incoming_message(message, rec_txt)
        if recent_chat_tail is None:
            recent_chat_tail = recent_tail_for_chat(message, _tail_n())
    except Exception as e:
        logger.debug("admin_bug tail/record: %s", e)

    raw_args = (command_args or "").strip()
    if raw_args.lower() in _BUG_HELP_ARGS:
        if zip_delivery == "to_admins_only":
            await reply_html_chunks(message, _USER_BUG_HELP_HTML)
        else:
            await reply_html_chunks(
                message,
                "\n".join(
                    [
                        "<b>🐞 /admin_bug — как писать команду</b>",
                        "",
                        "1) Ответьте <b>реплаем</b> на проблемное сообщение.",
                        "2) Отправьте одну из команд:",
                        "<code>/admin_bug</code>",
                        "<code>/admin_bug net</code>",
                        "<code>/admin_bug 60</code>",
                        "<code>/admin_bug comp=voice</code>",
                        "<code>/admin_bug net 50 comp=brain ожидал список инструментов, получил обрезанный ответ</code>",
                        "",
                        "Или текстом (админ): <code>зафиксируй баг</code> с теми же аргументами в хвосте.",
                        "Порядок аргументов: <code>net</code> → <code>N</code> (строк лога 1..100) → <code>comp=...</code> → заметка.",
                        "По умолчанию N берётся из <code>ADMIN_BUG_LOG_LINES</code> (обычно 80).",
                    ]
                ),
            )
        return

    include_net, log_n, log_comp, include_full_bundle, human_note = parse_admin_bug_command_args(command_args)
    if zip_delivery == "to_admins_only":
        include_net, log_n, log_comp, include_full_bundle, human_note = sanitize_user_bug_args(
            include_net, log_n, log_comp, include_full_bundle, human_note
        )
    try:
        await message.bot.send_chat_action(message.chat.id, "typing")
    except Exception as e:
        logger.debug('%s optional failed: %s', 'admin_bug_runner', e, exc_info=True)
    reply_to = message.reply_to_message
    if reply_to is None:
        await message.answer(
            sanitize_html("⚠️ Лучше запускать багрепорт <b>реплаем</b> на проблемное сообщение — так в отчёт попадёт точный контекст."),
            parse_mode="HTML",
        )
    net_h = " + проверка сети (~20 с)" if include_net else ""
    mode_h = ", полный bundle" if include_full_bundle else ", compact bundle"
    comp_h = f", компонент <code>{esc(log_comp)}</code>" if log_comp else ""
    src_lbl = ""
    if capture_source == "nl_phrase":
        src_lbl = " (NL)"
    elif capture_source in {"user_nl_phrase", "user_slash"}:
        src_lbl = " (пользователь)"
    if zip_delivery == "to_admins_only":
        await message.answer(
            sanitize_html(f"Формирую отчёт для разработчика{src_lbl}{net_h}: снимок логов <code>{log_n}</code> строк{comp_h}…"),
            parse_mode="HTML",
        )
    else:
        await message.answer(
            sanitize_html(f"Собираю багрепорт{src_lbl}{net_h}{mode_h}: диагностика + снимок логов (<code>{log_n}</code> строк{comp_h})…"),
            parse_mode="HTML",
        )
    bug_doc = build_bug_report_document(
        command_chat_id=message.chat.id,
        command_message_id=message.message_id,
        reporter_user=message.from_user,
        human_note=human_note,
        reply_to=reply_to,
        recent_chat_tail=recent_chat_tail,
        capture_source=capture_source,
    )
    try:
        bundle = await build_diagnostic_bundle(
            layer.orchestrator,
            layer._admin_module,
            include_connectivity=include_net,
        )
    except Exception as e:
        await message.answer(f"Не удалось собрать диагностику: {e}")
        return
    logs_snap = layer._admin_module.admin_logs_snapshot(log_n, component=log_comp)
    zbytes = admin_bug_report_zip_bytes(
        bundle,
        bug_report=bug_doc,
        logs_snapshot=logs_snap,
        include_full_bundle=include_full_bundle,
    )
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    fname = f"gemma_bugreport_{ts}.zip"
    bug_dir = Path("data/diagnostics/bug_reports")
    saved_s = "—"
    try:
        bug_dir.mkdir(parents=True, exist_ok=True)
        saved_path = bug_dir / fname
        saved_path.write_bytes(zbytes)
        saved_s = saved_path.as_posix()
    except Exception as e:
        saved_s = f"ошибка записи: {e}"
    tools_copy = copy_admin_zip_to_data_tools(zbytes, fname)
    n_tail = len(recent_chat_tail or [])
    cap = sanitize_html(
        "🐞 <b>Багрепорт</b>\n"
        f"Источник: <code>{esc(capture_source)}</code>\n"
        f"Сеть в архиве: <code>{'да' if include_net else 'нет'}</code>\n"
        f"Режим bundle: <code>{'full' if include_full_bundle else 'compact'}</code>\n"
        f"Лог (строк): <code>{log_n}</code>"
        + (f", фильтр: <code>{esc(log_comp)}</code>" if log_comp else "")
        + "\n"
        f"Реплай на сообщение: <code>{'да' if reply_to else 'нет'}</code>\n"
        f"Хвост чата в отчёте: <code>{n_tail}</code> запис.\n"
        f"Файл на сервере: <code>{esc(saved_s)}</code>"
    )
    if tools_copy:
        cap += f"\nКопия в data/tools: <code>{esc(tools_copy)}</code> — <code>/zip_read bundle.json</code>"
    cap += (
        "\n\nВнутри: <code>bundle.json</code>, <code>bug_report.json</code>, "
        "<code>logs_snapshot.json</code>, <code>logs_snapshot.txt</code>"
    )
    if zip_delivery == "to_admins_only":
        reporter_id = str(message.from_user.id) if message.from_user else ""
        recipients = [r for r in bug_report_forward_recipient_ids() if r and str(r) != reporter_id]
        if not recipients:
            await message.answer(
                sanitize_html(
                    "Приём отчётов сейчас недоступен (на сервере не настроены получатели). "
                    "Напишите администратору вручную."
                ),
                parse_mode="HTML",
            )
            return
        banner = _reporter_banner_html(message)
        cap_adm = sanitize_html(f"{banner}\n\n{cap}")
        if len(cap_adm) > 1000:
            cap_adm = cap_adm[:997] + "…"
        ok_any = False
        for rid in recipients:
            try:
                doc_adm = BufferedInputFile(zbytes, filename=fname)
                await message.bot.send_document(
                    chat_id=int(rid),
                    document=doc_adm,
                    caption=cap_adm,
                    parse_mode="HTML",
                )
                ok_any = True
            except Exception as e:
                logger.warning("user bug zip → admin %s: %s", rid, e)
        if ok_any:
            MONITOR.inc("bug_report_user_zip_forward_ok_total")
            await message.answer(
                sanitize_html("Спасибо, отчёт отправлен разработчику. Архив вам не присылаем — он только у администрации."),
                parse_mode="HTML",
            )
        else:
            MONITOR.inc("bug_report_user_zip_forward_fail_total")
            await message.answer(
                sanitize_html("Отчёт собран, но не удалось отправить разработчику. Попробуйте позже или напишите администратору."),
                parse_mode="HTML",
            )
        return

    doc = BufferedInputFile(zbytes, filename=fname)
    try:
        await message.answer_document(document=doc, caption=cap, parse_mode="HTML")
    except Exception as e:
        await message.answer(f"Архив собран, но отправка файла не удалась: {e}")


async def maybe_run_nl_bug_capture(layer: Any, message: Message, payload: str) -> bool:
    """
    «Зафиксируй баг» / bug report: админу — полный поток в чат; обычному пользователю в ЛС — только админам (если включено).
    """
    if not prose_wants_bug_report_capture(payload):
        return False
    remainder = bug_nl_args_remainder(payload)
    uid = str(message.from_user.id) if message.from_user else ""
    if layer._admin_module.is_admin(uid):
        MONITOR.inc("admin_bug_nl_phrase_total")
        await run_admin_bug_flow(
            layer,
            message,
            command_args=remainder or None,
            recent_chat_tail=None,
            capture_source="nl_phrase",
            zip_delivery="to_sender",
        )
        return True
    if str(message.chat.type) != "private":
        return False
    if not bug_report_user_submit_enabled():
        return False
    ok_cd, wait_sec = user_bug_cooldown_ok(uid)
    if not ok_cd:
        await message.answer(
            sanitize_html(f"Следующий отчёт можно отправить примерно через {wait_sec} с."),
            parse_mode="HTML",
        )
        return True
    MONITOR.inc("user_bug_nl_phrase_total")
    await run_admin_bug_flow(
        layer,
        message,
        command_args=remainder or None,
        recent_chat_tail=None,
        capture_source="user_nl_phrase",
        zip_delivery="to_admins_only",
    )
    return True
