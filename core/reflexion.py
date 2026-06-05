"""
Reflexion — рефлексирующий модуль для самообучения на ошибках self-verify.

Генерирует «уроки» из плохих исправлений, сохраняет их в памят
(через Mem0 add_structured_facts и локальный JSONL), возвращает
релевантные уроки в контекст промпта и обобщает их раз в час.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


# ── Локальное хранилище уроков (JSONL) ──

_LESSONS_PATH: Optional[str] = None
_LESSONS_LOCK = threading.Lock()


def _lessons_path() -> str:
    global _LESSONS_PATH
    if _LESSONS_PATH:
        return _LESSONS_PATH
    base = os.getenv("ERROR_ANALYSIS_DIR", os.path.join("data", "runtime"))
    p = os.path.join(base, "reflexion_lessons.jsonl")
    _LESSONS_PATH = p
    return p


def _load_lessons() -> List[Dict[str, Any]]:
    path = _lessons_path()
    if not os.path.isfile(path):
        return []
    lessons: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    lessons.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return lessons


def _append_lesson(lesson: Dict[str, Any]) -> None:
    path = _lessons_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        line = json.dumps(lesson, ensure_ascii=False, default=str) + "\n"
        with _LESSONS_LOCK:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
        logger.info("[reflexion] appended lesson path=%s bytes=%d", path, len(line.encode("utf-8")))
    except OSError as e:
        logger.warning("[reflexion] append lesson error: %s", e)


# ── Генерация урока ──


_REFLEXION_PROMPT = """Ты — аналитик, который учит ассистента на его ошибках.

Пользователь задал вопрос. Ассистент дал ответ. Система самопроверки
предложила исправление, но оно оказалось некачественным (мусорным).

Твоя задача: сформулировать один короткий «урок» — правило на русском языке,
которое поможет ассистенту в будущем не допускать подобных ошибок.

Формат: одна строка, 1-2 предложения, без пояснений.
Начинай с «Если» или «При».

Примеры:
- Если вопрос о физическом явлении, объясни механизм, а не просто назови факт.
- При запросе о цвете предмета укажи научную причину (длина волны, рассеяние).
- Если пользователь просит сравнение, приведи конкретные численные параметры.
"""


def _reflect_on_error_sync(
    user_text: str,
    original_reply: str,
    bad_fix: str,
) -> str:
    """Синхронный эвристический генератор урока (fallback без LLM).

    Используется как fallback, если LLM недоступен.
    Извлекает ключевые слова из запроса и формулирует шаблонный урок.
    """
    txt = (user_text or "").strip().lower()
    # Простейшая эвристика на основе первых слов запроса
    prefixes = {
        "почему": "объясни причину",
        "отчего": "объясни причину",
        "как": "объясни процесс по шагам",
        "зачем": "объясни цель",
        "что такое": "дай точное определение",
        "сравни": "приведи численные параметры",
        "рассчитай": "выполни точный расчёт",
        "спланируй": "составь пошаговый план",
        "сколько": "вычисли точное значение",
    }
    for prefix, hint in prefixes.items():
        if txt.startswith(prefix):
            return (
                f"Если вопрос начинается с «{prefix}», {hint}, "
                f"а не давай общее описание без конкретики."
            )
    # Если запрос содержит вопросительное слово
    for word, hint in [("или", "сравни варианты"), ("разниц", "укажи численную разницу")]:
        if word in txt:
            return f"При запросе с «{word}», {hint}."
    return (
        "Если модель самопроверки дала некачественное исправление, "
        "оставь оригинальный ответ без изменений."
    )


async def reflect_on_error(
    *,
    user_text: str,
    original_reply: str,
    bad_fix: str,
    llm: Any = None,
) -> str:
    """Сгенерировать урок из ошибки самопроверки.

    Пытается использовать LLM (быструю модель), при ошибке —
    синхронная эвристика.
    """
    if llm is not None:
        try:
            prompt = (
                f"Запрос пользователя: {user_text}\n"
                f"Ответ ассистента: {original_reply}\n"
                f"Мусорное исправление: {bad_fix}\n\n"
                f"Сформулируй урок для ассистента."
            )
            result = await asyncio.wait_for(
                llm.generate(
                    prompt=prompt,
                    system_prompt=_REFLEXION_PROMPT,
                    max_tokens=120,
                    temperature=0.3,
                ),
                timeout=10.0,
            )
            content = str(result.get("content", "") or "").strip()
            if content and len(content) > 10:
                return content
        except asyncio.TimeoutError:
            logger.debug("[reflexion] LLM timeout, using heuristic")
        except Exception as e:
            logger.debug("[reflexion] LLM error: %s", e)
    return _reflect_on_error_sync(user_text, original_reply, bad_fix)


# ── Сохранение урока ──


def store_lesson(*, lesson: str, user_id: str, memory: Any = None) -> None:
    """Сохранить урок в долговременную память.

    Пытается сохранить через Mem0 add_structured_facts с полем
    reflexion_lesson, плюс всегда пишет в локальный JSONL.
    """
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "lesson": lesson,
        "user_id": str(user_id),
        "type": "reflexion_lesson",
    }
    _append_lesson(row)

    # Сохраняем в Mem0 как структурированный факт
    if memory is not None and hasattr(memory, "add_structured_facts"):
        try:
            memory.add_structured_facts(
                str(user_id),
                [{"field": "reflexion_lesson", "content": lesson}],
            )
        except Exception as e:
            logger.debug("[reflexion] Mem0 store error: %s", e)


# ── Поиск релевантных уроков ──


def get_relevant_lessons(*, user_text: str, top_k: int = 3) -> List[str]:
    """Найти уроки, релевантные текущему запросу, из локального JSONL.

    Использует простое совпадение ключевых слов.
    """
    txt = (user_text or "").strip().lower()
    if not txt:
        return []

    all_lessons = _load_lessons()
    if not all_lessons:
        return []

    # Простой tf-подобный рейтинг: считаем пересечение слов
    words = set(txt.split())
    scored: List[tuple] = []
    for lesson_row in all_lessons[-100:]:  # последние 100
        lesson_text = str(lesson_row.get("lesson", "") or "")
        lesson_words = set(lesson_text.lower().split())
        overlap = len(words & lesson_words)
        if overlap > 0:
            scored.append((overlap, lesson_text))

    scored.sort(key=lambda x: -x[0])
    return [s[1] for s in scored[:top_k]]


# ── Синтез и дедупликация ──


def synthesize_lessons() -> Dict[str, Any]:
    """Просмотреть накопленные уроки, удалить дубликаты, обобщить.

    Возвращает статистику по синтезу.
    """
    all_lessons = _load_lessons()
    if not all_lessons:
        return {"status": "no_lessons", "removed": 0, "remaining": 0}

    before = len(all_lessons)

    # Дедупликация: группируем по тексту урока, оставляем последний
    seen: Dict[str, Dict] = {}
    for row in all_lessons:
        lesson = str(row.get("lesson", "") or "").strip()
        if not lesson:
            continue
        seen[lesson] = row

    # Сохраняем уникальные
    path = _lessons_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with _LESSONS_LOCK:
            with open(path, "w", encoding="utf-8") as f:
                for row in seen.values():
                    f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except OSError as e:
        logger.warning("[reflexion] synthesize write error: %s", e)
        return {"status": "error", "detail": str(e)}

    after = len(seen)
    removed = before - after

    logger.info(
        "[reflexion] synthesized: %d lessons → %d (removed %d duplicates)",
        before, after, removed,
    )

    return {
        "status": "ok",
        "before": before,
        "after": after,
        "removed": removed,
    }


# ── Периодический синтез ──


async def _reflexion_loop(interval_sec: int = 3600) -> None:
    """Раз в час запускать синтез уроков."""
    while True:
        try:
            await asyncio.sleep(interval_sec)
            synthesize_lessons()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("[reflexion] loop error: %s", e)


_REFLEXION_TASK: Optional[asyncio.Task] = None


def start_reflexion_loop(interval_sec: int = 3600) -> None:
    """Запустить фоновый цикл рефлексии."""
    global _REFLEXION_TASK
    if _REFLEXION_TASK is not None and not _REFLEXION_TASK.done():
        return
    _REFLEXION_TASK = asyncio.create_task(_reflexion_loop(interval_sec=interval_sec))
