"""
Уведомление администраторам в Telegram (личка) при запуске бота.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, List

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from core.boot_timeline import boot_timeline_snapshot
from core.report_i18n import ru_status, system_status_lamp
from core.report_timezone import format_operator_datetime, format_operator_datetime_from_iso
from core.telegram_ui import esc
from core.telegram_util import sanitize_html

logger = logging.getLogger(__name__)


def _startup_notify_enabled() -> bool:
    return os.getenv("ADMIN_STARTUP_NOTIFY", "true").strip().lower() in {"1", "true", "yes", "on"}


def _notify_recipient_ids() -> List[str]:
    raw = os.getenv("ADMIN_NOTIFY_USER_IDS", "").strip()
    if raw:
        return [x.strip() for x in raw.split(",") if x.strip()]
    raw2 = os.getenv("ADMIN_USER_IDS", "").strip()
    return [x.strip() for x in raw2.split(",") if x.strip()]


async def send_startup_dm_to_admins(bot: Bot, orchestrator: Any) -> None:
    if not _startup_notify_enabled():
        logger.info("ADMIN_STARTUP_NOTIFY выключен — стартовые ЛС не отправляются.")
        return
    ids = _notify_recipient_ids()
    if not ids:
        logger.info("Стартовое уведомление: нет ADMIN_USER_IDS / ADMIN_NOTIFY_USER_IDS.")
        return
    try:
        info = orchestrator.get_system_info()
    except Exception as e:
        info = {"error": str(e)}
    overall = str(info.get("overall_status", "unknown"))
    modules = info.get("modules") or []
    n_mod = len(modules) if isinstance(modules, list) else 0
    safe = False
    try:
        rc = getattr(orchestrator, "_resilience", None)
        if rc is not None and rc.is_enabled():
            safe = bool(rc.is_safe_mode())
    except Exception as e:
        logger.debug('%s optional failed: %s', 'startup_notify', e, exc_info=True)
    when = format_operator_datetime(datetime.now(timezone.utc))
    boot = boot_timeline_snapshot()
    bstate = boot.get("boot_state") if isinstance(boot.get("boot_state"), dict) else {}
    restarted = bool(bstate.get("restart_detected"))
    prev_boot = ""
    if restarted and bstate.get("previous_start_utc"):
        prev_boot = format_operator_datetime_from_iso(bstate.get("previous_start_utc"))
    lamp = system_status_lamp(orchestrator)
    st_ru = ru_status(overall)
    summary_lines = [
        f"🕐 Время: <code>{when}</code>",
        f"🔁 Перезапуск: <code>{'да' if restarted else 'нет'}</code>",
    ]
    if prev_boot:
        summary_lines.append(f"⏮ Прошлый старт: <code>{prev_boot}</code>")
    if bstate.get("boot_count") is not None:
        summary_lines.append(f"🔢 Номер запуска: <code>{esc(bstate.get('boot_count'))}</code>")
    summary_lines.extend(
        [
            f"🧩 Модулей в отчёте: <code>{n_mod}</code>",
            f"📡 Статус: <b>{esc(st_ru)}</b> <i>(<code>{esc(overall)}</code>)</i>",
            f"🛡 Безопасный режим: <code>{'да' if safe else 'нет'}</code>",
        ]
    )
    summary_block = "<blockquote>" + "\n".join(summary_lines) + "</blockquote>"
    cmd_lines = [
        "<code>/admin_system</code> <i>(HTML)</i> · <code>/admin_system_json</code> — сводка",
        "<code>/admin_pulse</code> · <code>/admin_pulse_json</code> — пульс и «рентген»",
        "<code>/admin_logs</code> или <code>/admin_logs 40</code> — хвост лога ошибок",
        "<code>/admin_health</code> · <code>/admin_operator</code> — здоровье и консоль оператора",
        "<code>/admin_usage_digest</code> — привычки и тренды <i>(авто при GEMMA_AUTOPILOT_MODE)</i>",
    ]
    cmd_block = "<blockquote>" + "\n".join(cmd_lines) + "</blockquote>"
    text = "\n".join(
        [
            f"{lamp} 🚀 <b>Бот запущен</b>",
            "",
            "📊 <b>Сводка</b>",
            summary_block,
            "",
            "🔗 <b>Команды отчёта</b>",
            cmd_block,
        ]
    )
    text = sanitize_html(text)
    per_send = float(os.getenv("TELEGRAM_STARTUP_DM_TIMEOUT_SEC", "45"))
    for uid in ids:
        try:
            await asyncio.wait_for(
                bot.send_message(chat_id=int(uid), text=text, parse_mode="HTML"),
                timeout=per_send,
            )
        except asyncio.TimeoutError:
            logger.warning("Стартовое ЛС: таймаут %ss user_id=%s", int(per_send), uid)
        except (TelegramForbiddenError, TelegramBadRequest, ValueError) as e:
            logger.warning("Стартовое ЛС не доставлено user_id=%s: %s", uid, e)
        except Exception as e:
            logger.warning("Стартовое ЛС ошибка user_id=%s: %s", uid, e)
