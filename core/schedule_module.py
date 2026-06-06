"""
Schedule Module - Расписание пользователя (персистентное JSON-хранилище).
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_schedule_lock = threading.Lock()

# Транслитерация популярных станций (BY/RU) для ссылок Яндекс / агрегаторов
_RAIL_STATION_SLUGS: Dict[str, str] = {
    "минск": "minsk",
    "minsk": "minsk",
    "михановичи": "mihanovichi",
    "mihanovichi": "mihanovichi",
    "брест": "brest",
    "brest": "brest",
    "гомель": "gomel",
    "gomel": "gomel",
    "витебск": "vitebsk",
    "vitebsk": "vitebsk",
    "могилев": "mogilev",
    "могилёв": "mogilev",
    "mogilev": "mogilev",
    "орша": "orsha",
    "orsha": "orsha",
    "барановичи": "baranovichi",
    "baranovichi": "baranovichi",
}


def _station_slug(name: str) -> str:
    k = (name or "").strip().lower()
    if k in _RAIL_STATION_SLUGS:
        return _RAIL_STATION_SLUGS[k]
    ascii_slug = re.sub(r"[^a-z0-9]+", "-", k, flags=re.IGNORECASE).strip("-")
    return ascii_slug or "unknown"


def _load_schedules() -> Dict[str, Dict[str, Any]]:
    from core.schedule_storage import load_all

    with _schedule_lock:
        return load_all()


def _save_schedules(data: Dict[str, Dict[str, Any]]) -> None:
    from core.schedule_storage import save_all

    with _schedule_lock:
        save_all(data)


class ScheduleModule:
    """Модуль управления расписанием (singleton на процесс через tools auto-discover)."""

    def __init__(self) -> None:
        self.schedules: Dict[str, Dict[str, Any]] = _load_schedules()

    def _persist(self) -> None:
        _save_schedules(self.schedules)

    def get_schedule(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Персональное расписание пользователя в боте (уроки/события). Не для расписания электричек по городам."""
        return self.schedules.get(user_id)

    def suburban_rail_schedule_links(
        self,
        origin: str,
        destination: str,
        country_hint: str = "BY",
        user_id: Optional[str] = None,
        **_: Any,
    ) -> Dict[str, Any]:
        """
        Готовые ссылки на расписание пригорода/электричек между двумя пунктами (как «быстрый» ответ без парсинга API).
        Для пользовательского запроса «Минск — пригород» и т.п.
        user_id и прочие поля пробрасывает brain для всех инструментов — игнорируем.
        """
        o = (origin or "").strip()
        d = (destination or "").strip()
        if not o or not d:
            return {"error": "Нужны origin и destination — названия станций или городов."}
        o_s = _station_slug(o)
        d_s = _station_slug(d)
        q = urllib.parse.quote(f"расписание электричек {o} {d}")
        links: List[Dict[str, str]] = [
            {
                "title": f"Яндекс.Расписания (пригород) {o} → {d}",
                "url": f"https://rasp.transit.example.com/suburban/{d_s}--{o_s}/today",
            },
            {
                "title": f"Яндекс.Расписания (обратный порядок станций)",
                "url": f"https://rasp.transit.example.com/suburban/{o_s}--{d_s}/today",
            },
            {
                "title": "Poezdato",
                "url": f"https://poezdato.net/raspisanie-poezdov/{o_s}--{d_s}/",
            },
            {
                "title": "Поиск (Яндекс)",
                "url": f"https://transit.example.com/search/?text={q}",
            },
        ]
        return {
            "origin": o,
            "destination": d,
            "country_hint": (country_hint or "BY").strip().upper(),
            "links": links,
            "hint": "Откройте подходящую ссылку; если маршрут не открылся — выберите станции вручную на rasp.transit.example.com. "
            "get_schedule(user_id) — только для внутреннего расписания пользователя в боте, не для электричек.",
        }

    def update_schedule(self, user_id: str, data: Dict[str, Any]) -> bool:
        """Обновить расписание"""
        if user_id not in self.schedules:
            self.schedules[user_id] = {"events": []}
        self.schedules[user_id].update(data)
        self._persist()
        return True

    def add_event(self, user_id: str, event: Any) -> bool:
        """Добавить событие в расписание"""
        if isinstance(event, str):
            event = {"title": event, "time": "", "created_at": datetime.now().isoformat(timespec="seconds")}
        if not isinstance(event, dict):
            return False
        if user_id not in self.schedules:
            self.schedules[user_id] = {"events": []}
        self.schedules[user_id].setdefault("events", []).append(event)
        self._persist()
        try:
            from core.reminder_dispatch import persist_reminder_from_schedule_event

            persist_reminder_from_schedule_event(user_id, event)
        except Exception as e:
            logger.debug("reminder persist from schedule: %s", e)
        return True
