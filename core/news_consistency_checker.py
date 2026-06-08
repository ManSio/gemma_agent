"""Turn-level news consistency checker — detects contradictions between dialogue turns."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Простой regex-based NER для извлечения сущностей
_DATE_PATTERNS = [
    re.compile(r"\b(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+(\d{4})\b", re.I),
    re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b"),
    re.compile(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b"),
    re.compile(r"\b(?:вчера|сегодня|завтра|позавчера|послезавтра)\b", re.I),
    re.compile(r"\b(?:январь|февраль|март|апрель|май|июнь|июль|август|сентябрь|октябрь|ноябрь|декабрь)\s+(\d{4})\b", re.I),
]

_NAME_PATTERN = re.compile(
    r"\b([А-ЯЁ][а-яё\-]{2,})\s+([А-ЯЁ][а-яё\-]{2,})\b"
)
_LOCATION_PATTERN = re.compile(
    r"\b(?:в\s+|из\s+|на\s+|у\s+|по\s+|за\s+|до\s+)?([А-ЯЁ][а-яё\-]{2,})(?=\s*[,\.\?!:;]|\s|$)",
    re.I,
)


class NewsConsistencyConflict:
    """Один обнаруженный конфликт."""

    __slots__ = ("previous_statement", "turn_index", "new_statement", "field")

    def __init__(
        self,
        previous_statement: str,
        turn_index: int,
        new_statement: str,
        field: str = "unknown",
    ) -> None:
        self.previous_statement = previous_statement
        self.turn_index = turn_index
        self.new_statement = new_statement
        self.field = field

    def to_dict(self) -> Dict[str, Any]:
        return {
            "previous_statement": self.previous_statement,
            "turn_index": self.turn_index,
            "new_statement": self.new_statement,
            "field": self.field,
        }


class NewsConsistencyChecker:
    """Проверка, что в одной сессии факты не противоречат."""

    MAX_LOOKBACK_TURNS = 5
    MIN_TEXT_LENGTH_FOR_CHECK = 60

    async def check_dialogue_consistency(
        self,
        user_id: str,
        recent_dialogue: List[Dict[str, Any]],
        new_reply: str,
        new_sources: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Проверить консистентность нового ответа с предыдущими в диалоге.

        Args:
            user_id: ID пользователя.
            recent_dialogue: Список последних turn'ов (каждый: {"user": str, "bot": str, "index": int}).
            new_reply: Новый ответ бота.
            new_sources: Источники нового ответа (опционально).

        Returns:
            {
                "consistent": True/False,
                "conflicts": [NewsConsistencyConflict, ...],
                "recommendation": "safe" | "warn_user" | "needs_fix"
            }
        """
        if not new_reply or len(new_reply) < self.MIN_TEXT_LENGTH_FOR_CHECK:
            return self._safe_result()

        conflicts: List[NewsConsistencyConflict] = []
        new_entities = self.extract_entities(new_reply)

        if not new_entities:
            return self._safe_result()

        # Собрать сущности из предыдущих turn'ов
        for i, turn in enumerate(recent_dialogue[-self.MAX_LOOKBACK_TURNS:]):
            bot_text = str(turn.get("bot", "") or "")
            user_text = str(turn.get("user", "") or "")
            combined = bot_text + " " + user_text
            if len(combined) < self.MIN_TEXT_LENGTH_FOR_CHECK:
                continue
            prev_entities = self.extract_entities(combined)
            turn_conflicts = self._find_conflicts(
                prev_entities, new_entities, turn_index=i
            )
            conflicts.extend(turn_conflicts)

        if not conflicts:
            return self._safe_result()

        recommendation = self._determine_recommendation(conflicts)

        return {
            "consistent": False,
            "conflicts": [c.to_dict() for c in conflicts],
            "recommendation": recommendation,
            "user_id": user_id,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    def extract_entities(self, text: str) -> Dict[str, List[str]]:
        """
        Извлечь именованные сущности из текста.

        Returns:
            {"dates": [...], "people": [...], "locations": [...]}
        """
        result: Dict[str, List[str]] = {
            "dates": [],
            "people": [],
            "locations": [],
        }
        if not text or not isinstance(text, str):
            return result

        # Даты
        for pattern in _DATE_PATTERNS:
            for m in pattern.finditer(text):
                date_str = m.group(0).strip().lower()
                if date_str not in result["dates"]:
                    result["dates"].append(date_str)

        # Имена
        for m in _NAME_PATTERN.finditer(text):
            name = m.group(0).strip()
            if 5 <= len(name) <= 60 and name not in result["people"]:
                result["people"].append(name)

        # Локации (простые)
        for m in _LOCATION_PATTERN.finditer(text):
            loc = m.group(1).strip().strip(",").strip()
            if 3 <= len(loc) <= 40 and loc not in result["locations"]:
                # Исключить стоп-слова и месяцы
                _stop_locs = {
                    "этом", "своем", "нашем", "вашем", "марта", "апреля",
                    "мая", "июня", "июля", "августа", "сентября", "октября",
                    "ноября", "декабря", "января", "февраля", "года",
                    "сайте", "сегодня", "вчера", "завтра", "сейчас",
                    "сообщил", "сказал", "заявил", "рассказал",
                    "чрезвычайное", "положение", "центральном", "районе",
                    "прошлогодней", "ситуация", "отличается", "событие",
                }
                if loc.lower() not in _stop_locs:
                    result["locations"].append(loc)

        return result

    def _find_conflicts(
        self,
        prev_entities: Dict[str, List[str]],
        new_entities: Dict[str, List[str]],
        *,
        turn_index: int,
    ) -> List[NewsConsistencyConflict]:
        """Найти противоречия между двумя наборами сущностей."""
        conflicts: List[NewsConsistencyConflict] = []

        for field in ("dates", "people", "locations"):
            prev_set = set(prev_entities.get(field, []))
            new_set = set(new_entities.get(field, []))

            if not prev_set or not new_set:
                continue

            # Если в предыдущем и новом ответе есть общая тема
            # (пересекающиеся люди или локации) — проверяем даты
            if field in ("people", "locations"):
                common = prev_set & new_set
                if common:
                    # Есть общие сущности — проверяем dates на конфликт
                    prev_dates = set(prev_entities.get("dates", []))
                    new_dates = set(new_entities.get("dates", []))
                    if prev_dates and new_dates and prev_dates != new_dates:
                        for pd in prev_dates:
                            for nd in new_dates:
                                if pd != nd and self._dates_contradict(pd, nd):
                                    conflicts.append(
                                        NewsConsistencyConflict(
                                            previous_statement=pd,
                                            turn_index=turn_index,
                                            new_statement=nd,
                                            field="date_conflict",
                                        )
                                    )

        return conflicts

    def _dates_contradict(self, date1: str, date2: str) -> bool:
        """True если даты противоречат друг другу (разные даты одного события)."""
        if not date1 or not date2:
            return False
        if date1 == date2:
            return False
        # Вчера/сегодня/завтра не считается противоречием
        rel_dates = {"вчера", "сегодня", "завтра", "позавчера", "послезавтра"}
        if date1 in rel_dates or date2 in rel_dates:
            return False
        # Разные годы
        y1 = re.search(r"(\d{4})", date1)
        y2 = re.search(r"(\d{4})", date2)
        if y1 and y2 and y1.group(1) != y2.group(1):
            return True
        return False

    def _determine_recommendation(
        self, conflicts: List[NewsConsistencyConflict]
    ) -> str:
        """Определить рекомендацию по типу конфликтов."""
        date_conflicts = sum(1 for c in conflicts if c.field == "date_conflict")
        if date_conflicts >= 2:
            return "needs_fix"
        if date_conflicts == 1:
            return "warn_user"
        return "warn_user"

    def _safe_result(self) -> Dict[str, Any]:
        """Пустой безопасный результат."""
        return {
            "consistent": True,
            "conflicts": [],
            "recommendation": "safe",
        }