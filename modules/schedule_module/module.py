"""
Schedule Module — slash-расписание (plugin).

Хранилище: core.schedule_storage (user_schedules.json). NL — core.schedule_nl.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List

from core.models import Output
from core.schedule_storage import get_user_plugin_view, set_user_from_plugin
from core.user_facing_plain import format_schedule_plain

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_time_str(raw: str) -> str:
    s = (raw or "").strip()
    m = re.match(r"(\d{1,2})[:.]?(\d{2})?", s)
    if m:
        h = int(m.group(1))
        mi = int(m.group(2)) if m.group(2) else 0
        return f"{min(max(h, 0), 23):02d}:{min(max(mi, 0), 59):02d}"
    return s or "00:00"


class ScheduleModule:
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}

    async def execute(self, args: Dict[str, Any]) -> List[Output]:
        input_data = args.get("input", {})
        payload = (input_data.get("payload") or "").strip()

        if payload.startswith("/set_schedule "):
            parts = payload[14:].split(" ", 1)
            if len(parts) == 2:
                user_id = parts[0]
                try:
                    schedule_data = json.loads(parts[1])
                except json.JSONDecodeError:
                    return [Output(type="text", payload="Неверный JSON для расписания.")]
                if set_user_from_plugin(user_id, schedule_data):
                    return [Output(type="text", payload=f"Расписание пользователя {user_id} установлено.")]
                return [Output(type="text", payload=f"Ошибка установки расписания для {user_id}.")]

        elif payload.startswith("/get_schedule "):
            user_id = payload[14:].strip()
            s = get_user_plugin_view(user_id)
            if s:
                return [Output(type="text", payload=format_schedule_plain(s))]
            return [Output(type="text", payload=f"Расписание пользователя {user_id} не найдено.")]

        elif payload.startswith("/remind "):
            parts = payload[8:].split(" ", 2)
            if len(parts) >= 2:
                user_id = parts[0]
                event = parts[1]
                time_str = parts[2] if len(parts) > 2 else "09:00"
                time_str = _parse_time_str(time_str)
                ok = self.remind_user(user_id, event, time_str)
                if ok:
                    return [Output(type="text", payload=f"Напоминание установлено для {user_id}: {event} в {time_str}.")]
                return [Output(type="text", payload=f"Ошибка установки напоминания для {user_id}.")]
            return [Output(type="text", payload="/remind <user_id> <событие> [время HH:MM]")]

        elif payload.startswith("/next_lesson "):
            user_id = payload[13:].strip()
            result = self.get_next_lesson(user_id)
            if result.get("error"):
                return [Output(type="text", payload=result["error"])]
            return [Output(type="text", payload=json.dumps(result, ensure_ascii=False, indent=2))]

        return [
            Output(
                type="text",
                payload="/set_schedule <id> <json> | /get_schedule <id> | /remind <id> <event> [time] | /next_lesson <id>",
            )
        ]

    def set_schedule(self, user_id: str, schedule_data: Dict[str, Any]) -> bool:
        try:
            return set_user_from_plugin(user_id, schedule_data)
        except Exception:
            logger.exception("[schedule] set_schedule failed user_id=%s", user_id)
            return False

    def get_schedule(self, user_id: str) -> Dict[str, Any]:
        try:
            return get_user_plugin_view(user_id)
        except Exception:
            logger.exception("[schedule] get_schedule failed user_id=%s", user_id)
            return {}

    def remind_user(self, user_id: str, event: str, time_str: str) -> bool:
        try:
            schedule = self.get_schedule(user_id)
            if not schedule:
                self.set_schedule(user_id, {"reminders": []})
                schedule = self.get_schedule(user_id) or {}
            reminders: list = (schedule.get("schedule", {}) or {}).get("reminders", [])
            reminder_row = {
                "event": event,
                "time": _parse_time_str(time_str),
                "created_at": _now_iso(),
            }
            reminders.append(reminder_row)
            inner = dict(schedule.get("schedule") or {})
            inner["reminders"] = reminders
            ok_save = set_user_from_plugin(user_id, inner)
            if ok_save:
                try:
                    from core.reminder_dispatch import persist_reminder_from_schedule_event

                    persist_reminder_from_schedule_event(
                        user_id,
                        f"в {_parse_time_str(time_str)} {event}",
                    )
                except Exception as e:
                    logger.debug("remind_user persist: %s", e)
            return ok_save
        except Exception:
            logger.exception("[schedule] remind_user failed user_id=%s event=%s", user_id, event)
            return False

    def get_next_lesson(self, user_id: str) -> Dict[str, Any]:
        try:
            schedule = self.get_schedule(user_id)
            if not schedule:
                return {"error": "Расписание не найдено."}
            lessons = (schedule.get("schedule", {}) or {}).get("lessons", [])
            if not lessons:
                return {"error": "Нет уроков в расписании."}
            now = datetime.now()
            now_str = now.strftime("%H:%M")
            candidates = [(l.get("time", "23:59"), l) for l in lessons if l.get("time", "23:59") > now_str]
            if candidates:
                candidates.sort(key=lambda x: x[0])
                return candidates[0][1]
            return lessons[0]
        except Exception:
            logger.exception("[schedule] get_next_lesson failed user_id=%s", user_id)
            return {"error": "Ошибка получения расписания."}
