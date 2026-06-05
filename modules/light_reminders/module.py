"""Локальные напоминания (JSON). Доставка — reminder_dispatch (autopilot + soon-wake)."""

from __future__ import annotations

import time
from typing import Any, Dict, List

from core.light_slash import parse_slash_args
from core.models import Output
from core.reminder_dispatch import (
    add_reminder,
    cancel_reminder_by_list_index,
    list_active_reminders_sorted,
    load_reminders,
)


class LightRemindersModule:
    async def execute(self, args: dict):
        input_data = args.get("input") or {}
        context = args.get("context") or {}
        uid = str(context.get("user_id") or "unknown")
        payload = str(input_data.get("payload") or "")
        cmd, rest = parse_slash_args(payload)

        if cmd == "radd":
            parts = rest.split(maxsplit=1)
            if len(parts) < 2:
                return Output(
                    type="text",
                    payload="Формат: /radd <минуты> <текст>\nПример: /radd 45 позвонить маме",
                    meta={"module": "light_reminders"},
                )
            try:
                minutes = int(parts[0].strip())
            except ValueError:
                return Output(
                    type="text",
                    payload="Первый аргумент — целое число минут. Пример: /radd 30 купить воду",
                    meta={"module": "light_reminders"},
                )
            if minutes < 1 or minutes > 525600:
                return Output(
                    type="text",
                    payload="Минута: от 1 до 525600 (год).",
                    meta={"module": "light_reminders"},
                )
            text = parts[1].strip()
            if not text:
                return Output(type="text", payload="Добавь текст напоминания.", meta={"module": "light_reminders"})
            due = int(time.time()) + minutes * 60
            rid = add_reminder(uid, text, due)
            data = load_reminders()
            n = len((data.get("users") or {}).get(uid) or [])
            return Output(
                type="text",
                payload=f"Ок #{n}: через {minutes} мин — «{text}» (id {rid}). /rlist — список, /rnow — что уже пора.",
                meta={"module": "light_reminders"},
            )

        data = load_reminders()
        items: List[Dict[str, Any]] = list((data.get("users") or {}).get(uid) or [])

        if cmd == "rlist":
            if not items:
                return Output(type="text", payload="Напоминаний нет. /radd N текст", meta={"module": "light_reminders"})
            lines = []
            now = int(time.time())
            for i, it in enumerate(list_active_reminders_sorted(uid), start=1):
                due = int(it.get("due_ts") or 0)
                left = max(0, due - now) // 60
                flag = "⏰" if due <= now else f"через ~{left}м"
                lines.append(f"{i}. [{flag}] {it.get('text', '')} (id {it.get('id', '')})")
            return Output(type="text", payload="\n".join(lines), meta={"module": "light_reminders"})

        if cmd == "rdel":
            if not rest.isdigit():
                return Output(type="text", payload="Укажи номер строки из /rlist: /rdel 2", meta={"module": "light_reminders"})
            n = int(rest)
            if n < 1:
                return Output(type="text", payload="Неверный номер.", meta={"module": "light_reminders"})
            active = list_active_reminders_sorted(uid)
            if n > len(active):
                return Output(type="text", payload="Неверный номер. /rlist — актуальный список.", meta={"module": "light_reminders"})
            cnt, labels = cancel_reminder_by_list_index(uid, n)
            if cnt <= 0:
                return Output(type="text", payload="Не удалось удалить.", meta={"module": "light_reminders"})
            return Output(
                type="text",
                payload=f"Удалено №{n}: «{labels[0]}»",
                meta={"module": "light_reminders"},
            )

        if cmd == "rnow":
            now = int(time.time())
            due_items = [it for it in items if int(it.get("due_ts") or 0) <= now]
            if not due_items:
                return Output(
                    type="text",
                    payload="Ничего не просрочено. /rlist — все активные.",
                    meta={"module": "light_reminders"},
                )
            lines = [f"• {it.get('text', '')} (id {it.get('id', '')})" for it in sorted(due_items, key=lambda x: int(x.get("due_ts") or 0))]
            return Output(
                type="text",
                payload="Пора или просрочено:\n" + "\n".join(lines),
                meta={"module": "light_reminders"},
            )

        return Output(
            type="text",
            payload="/radd N текст — через N минут\n/rlist\n/rdel номер\n/rnow — что пора",
            meta={"module": "light_reminders"},
        )
