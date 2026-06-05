"""Память повторяющихся сценариев → эфемерный урок после порога."""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from threading import Lock
from typing import Dict, List

from core.runtime_telegram_settings import effective_bool

logger = logging.getLogger(__name__)
_LOCK = Lock()

_SCENARIO_LESSON_MAP: Dict[str, str] = {
    "finance_not_city": "Не спрашивай подтверждение города на инвестиционных/портфельных вопросах.",
    "reminder_word_in_prose": "Слово «напоминание» в статье — не команда создать reminder.",
    "duplicate_substantive_outputs": "На один вопрос — один связный ответ, не два несвязанных монолога.",
    "pre_send_empty": "Не отправляй пустой ответ; дай честный fallback или краткий переспрос.",
    "pre_send_leak": "Не пересказывай промпт, tools и document_intake — только ответ пользователю.",
    "situation_equation": "Уравнение: итог x=…, не только число из правой части.",
    "situation_translation": "Перевод — только целевым языком, без английского вместо запрошенного.",
}


def _enabled() -> bool:
    return effective_bool("SCENARIO_MEMORY_ENABLED", default=True)


def _threshold() -> int:
    try:
        return max(2, int((os.getenv("SCENARIO_MEMORY_REPEAT_THRESHOLD") or "3").strip()))
    except ValueError:
        return 3


def _store_path() -> Path:
    root = Path(os.getenv("GEMMA_PROJECT_ROOT") or ".").resolve()
    return root / "data" / "runtime" / "scenario_counts.json"


def _load() -> Dict[str, Dict[str, int]]:
    p = _store_path()
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save(data: Dict[str, Dict[str, int]]) -> None:
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")


def record_scenario_hits(user_id: str, scenario_ids: List[str]) -> None:
    if not _enabled() or not user_id or not scenario_ids:
        return
    uid = str(user_id)
    with _LOCK:
        data = _load()
        bucket = data.get(uid)
        if not isinstance(bucket, dict):
            bucket = {}
        for sid in scenario_ids:
            sid = (sid or "").strip()
            if not sid:
                continue
            bucket[sid] = int(bucket.get(sid) or 0) + 1
        data[uid] = bucket
        _save(data)


def maybe_autolearn_from_scenarios(user_id: str, scenario_ids: List[str]) -> None:
    if not _enabled() or not user_id:
        return
    uid = str(user_id)
    thr = _threshold()
    with _LOCK:
        data = _load()
        bucket = data.get(uid) if isinstance(data.get(uid), dict) else {}
    for sid in scenario_ids:
        instruction = _SCENARIO_LESSON_MAP.get(sid)
        if not instruction:
            continue
        if int((bucket or {}).get(sid) or 0) < thr:
            continue
        try:
            from core.ephemeral_lessons import add_lesson

            add_lesson(
                trigger=f"scenario:{sid}",
                instruction=instruction,
                match_regex=False,
                meta={"source": "scenario_memory", "ts": time.time()},
            )
            logger.info("[scenario_memory] autolearn user=%s scenario=%s", uid, sid)
        except Exception as e:
            logger.debug("scenario_memory autolearn: %s", e)
