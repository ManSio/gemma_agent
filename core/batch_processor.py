"""
Parallel Batch Processor — асинхронное исполнение batch-пунктов.

Архитектура:
  - run_parallel_batch(items, user_id, user_facts) -> dict
  - Адаптивный scheduler: подстраивает параллельность под API rate limits
  - Семафор + backoff на каждый сабвызов (защита от 429)
  - Только cheap-модель для сабвызовов (llm_generate_tiered)
  - return_exceptions=True: ошибка одного пункта не ломает остальные
  - User facts read-only (блокировка write на время параллельного прогона)
  - Fallback на sequential при ошибках или в начале обучения
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from core.brain.runtime import _llm
from core.llm_tiered import llm_generate_tiered
from core.monitoring import MONITOR

logger = logging.getLogger(__name__)

# ── Конфигурация ──

_PARALLEL_INITIAL = int(os.getenv("BATCH_PARALLEL_INITIAL", "2"))
_PARALLEL_MAX_CAP = int(os.getenv("BATCH_PARALLEL_MAX_CAP", "12"))
_PARALLEL_MEMORY_MARGIN = float(os.getenv("BATCH_PARALLEL_MEMORY_MARGIN", "0.2"))
_PARALLEL_MAX_TOKENS = int(os.getenv("BATCH_PARALLEL_MAX_TOKENS", "2000"))
_PARALLEL_TIMEOUT_SEC = float(os.getenv("BATCH_PARALLEL_TIMEOUT_SEC", "30.0"))
_PARALLEL_ENABLED = os.getenv("BATCH_PARALLEL_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
_MIN_ITEMS_FOR_PARALLEL = int(os.getenv("BATCH_PARALLEL_MIN_ITEMS", "3"))
_MIN_ELAPSED_MS = float(os.getenv("BATCH_PARALLEL_MIN_ELAPSED_MS", "200"))
# Пороги для scheduler
_SUCCESS_THRESHOLD = 0.8
_CREEP_INTERVAL = 5
_RATE_LIMIT_BACKOFF_SEC = float(os.getenv("BATCH_PARALLEL_RATE_LIMIT_BACKOFF", "30.0"))


# ── Scheduler (адаптивная параллельность) ──

_scheduler_lock = threading.Lock()
_scheduler_state: Dict[str, Any] = {
    "max_parallel": _PARALLEL_INITIAL,
    "consecutive_success": 0,
    "total_batches": 0,
    "total_errors": 0,
    "total_429": 0,
    "last_backoff_ts": 0.0,
}


def _get_max_parallel() -> int:
    with _scheduler_lock:
        return _scheduler_state["max_parallel"]


def _record_batch_result(errors: int, rate_limited: int, total: int) -> None:
    """Обновить scheduler на основе результата батча."""
    with _scheduler_lock:
        s = _scheduler_state
        s["total_batches"] += 1
        s["total_errors"] += errors
        s["total_429"] += rate_limited

        if errors > 0 or rate_limited > 0:
            success_rate = (total - errors) / max(total, 1)
            if success_rate < _SUCCESS_THRESHOLD or rate_limited > 0:
                new_val = max(1, s["max_parallel"] // 2)
                s["max_parallel"] = new_val
                s["consecutive_success"] = 0
                if rate_limited > 0:
                    s["last_backoff_ts"] = time.time()
                logger.info(
                    "[batch_scheduler] reduced parallelism to %d (errors=%d, 429=%d, success_rate=%.2f)",
                    new_val, errors, rate_limited, success_rate,
                )
            else:
                s["consecutive_success"] = 0
        else:
            s["consecutive_success"] += 1
            if s["consecutive_success"] >= _CREEP_INTERVAL and s["max_parallel"] < _PARALLEL_MAX_CAP:
                new_val = s["max_parallel"] + 1
                s["max_parallel"] = new_val
                s["consecutive_success"] = 0
                logger.info("[batch_scheduler] increased parallelism to %d", new_val)

        # Проверка памяти
        try:
            import psutil
            mem = psutil.virtual_memory()
            if mem.percent > (1.0 - _PARALLEL_MEMORY_MARGIN) * 100:
                new_val = max(1, s["max_parallel"] - 2)
                if new_val < s["max_parallel"]:
                    s["max_parallel"] = new_val
                    s["consecutive_success"] = 0
                    logger.warning("[batch_scheduler] low memory, reduced parallelism to %d", new_val)
        except ImportError:
            pass


# ── Dependency check ──

# Маркеры зависимости внутри одного пункта
_INTERNAL_DEPENDENCY_MARKERS: frozenset = frozenset({
    "предыдущ", "вышеупом", "на основе", "исходя из",
    "после того", "затем", "учитывая", "принимая во внимание",
    "с учётом", "с учетом", "в контексте", "как указано",
    "following", "based on", "given the", "considering",
})

# Маркеры анафоры / перекрёстной ссылки между пунктами
_CROSS_REF_MARKERS: frozenset = frozenset({
    "его", "её", "их", "него", "нее", "них", "нему", "ней", "ним",
    "этот", "эта", "это", "эти", "такой", "такая", "такое", "такие",
    "данный", "данная", "данное",
    "вышеуказан", "вышеописан", "вышеупом",
    "ранее упомянут", "ранее описан",
    "предыдущий", "предыдущая", "предыдущее", "предыдущие",
    "следующий", "следующая", "следующее",
    "соотве", "соответств",
    "аналог", "подобн",
    "same", "similar", "above", "below", "previous", "following",
    "latter", "former", "aforementioned", "aforesaid",
})

# Маркеры сравнения между пунктами — проверяем через корни слов
_COMPARATIVE_ROOTS: frozenset = frozenset({
    "сравн",  # сравни, сравнить, сравнение
    "в отличие",  # в отличие от
    "разниц",  # разница между
})

# Слова-дейктики: если пункт начинается с них — почти всегда ссылка на предыдущий
_DEICTIC_STARTS: frozenset = frozenset({
    "а теперь", "а что", "а как", "тогда",
    "and now", "what about", "how about",
    "наоборот", "в opposite",
})


def _has_dependency_within_item(item: str) -> bool:
    """Проверить, есть ли маркеры зависимости внутри одного пункта."""
    low = (item or "").lower().strip()
    if not low:
        return True  # пустой пункт = подозрительный
    # Прямые маркеры
    for marker in _INTERNAL_DEPENDENCY_MARKERS:
        if low.startswith(marker) or marker in low[:80]:
            return True
    # Дейктические старты
    for marker in _DEICTIC_STARTS:
        if low.startswith(marker):
            return True
    return False


def _has_cross_reference(items: List[str], idx: int) -> bool:
    """Проверить, ссылается ли пункт idx на предыдущий."""
    if idx == 0:
        return False
    item = (items[idx] or "").lower().strip()
    if not item:
        return False
    prev = (items[idx - 1] or "").lower().strip()
    if not prev:
        return False
    # 1. Прямые маркеры cross-ref (с исключением вопросительного контекста)
    for marker in _CROSS_REF_MARKERS:
        if marker in item[:60]:
            # Исключение: "что такое", "как такой" — это вопросы, не анафора
            if marker in ("такой", "такая", "такое", "такие") and (
                "что " + marker in item[:40] or "как " + marker[:3] in item[:40]
            ):
                continue
            return True
    # 2. Корни сравнения (сравн, отлич)
    for marker in _COMPARATIVE_ROOTS:
        if marker in item:
            return True
    # 3. Анафора: пункт начинается с местоимения/дейктика
    if _starts_with_pronoun(item, prev):
        return True
    return False


def _starts_with_pronoun(item: str, prev: str) -> bool:
    """Проверить, начинается ли item с анафорического местоимения (он/она/оно/это)."""
    pronouns = (
        "он ", "она ", "оно ", "они ", "это ", "эта ", "этот ", "эти ",
        "такой ", "такая ", "такое ",
    )
    for p in pronouns:
        if item.startswith(p):
            return True
    return False


def is_parallel_eligible(items: List[str]) -> bool:
    """Проверить, можно ли параллелить пункты.

    Проверяет три уровня независимости:
      1. Каждый пункт не содержит внутренних маркеров зависимости
         (предыдущ, на основе, исходя из, following, based on).
      2. Пункты не имеют перекрёстных ссылок друг на друга
         (анафора: "его", "этот", "данный"; сравнение: "сравни с", "в отличие от").
      3. Пункты не начинаются с дейктических маркеров
         ("а теперь", "а что", "тогда").
    """
    if not items or len(items) < _MIN_ITEMS_FOR_PARALLEL:
        return False

    for i, item in enumerate(items):
        if _has_dependency_within_item(item):
            return False
        if _has_cross_reference(items, i):
            return False
    return True


# ── Semaphore ──

_item_semaphore = asyncio.Semaphore(_PARALLEL_INITIAL)


def _refresh_semaphore() -> None:
    global _item_semaphore
    new_val = _get_max_parallel()
    _item_semaphore = asyncio.Semaphore(new_val)


# ── Single item execution ──

async def _run_single_item(
    item: str,
    index: int,
    total: int,
    user_id: str,
    user_facts_context: str,
    time_hint: str,
    calendar_hint: str,
    profile_hint: str,
) -> Tuple[int, Optional[str]]:
    """Выполнить один пункт batch-запроса. Возвращает (index, ответ или None при ошибке)."""
    async with _item_semaphore:
        # Проверка backoff после rate limit
        with _scheduler_lock:
            backoff_until = _scheduler_state.get("last_backoff_ts", 0.0) + _RATE_LIMIT_BACKOFF_SEC
        if backoff_until > time.time():
            wait = backoff_until - time.time()
            logger.info("[batch_processor] rate-limit backoff: waiting %.1fs", wait)
            await asyncio.sleep(wait)

        _abstract_note = ""
        if not user_facts_context:
            _abstract_note = (
                "Задача самодостаточна: не подставляй город/страну пользователя, "
                "если вопрос абстрактный (математика, физика, головоломка).\n"
            )
        prompt = (
            f"Ты — полезный ассистент. Ответь на пункт {index} из {total} списка.\n\n"
            f"{user_facts_context}\n"
            f"{_abstract_note}"
            f"{calendar_hint}\n"
            f"{time_hint}\n"
            f"{profile_hint}\n\n"
            f"Пункт {index}: {item}\n\n"
            f"Ответь кратко, по существу."
        )
        system_prompt = "Ты — полезный ассистент. Отвечай кратко и по делу."

        try:
            result = await asyncio.wait_for(
                llm_generate_tiered(
                    _llm,
                    tag="batch_parallel_item",
                    prompt=prompt,
                    system_prompt=system_prompt,
                    max_tokens=_PARALLEL_MAX_TOKENS,
                    temperature=0.3,
                    base_timeout=_PARALLEL_TIMEOUT_SEC,
                ),
                timeout=_PARALLEL_TIMEOUT_SEC + 5.0,
            )
        except asyncio.TimeoutError:
            logger.warning("[batch_processor] item %d timeout", index)
            MONITOR.inc("batch_item_timeout_total")
            return index, None
        except Exception as e:
            logger.warning("[batch_processor] item %d error: %s", index, e)
            MONITOR.inc("batch_item_error_total")
            estr = str(e).lower()
            if "429" in estr or "rate limit" in estr or "too many requests" in estr:
                MONITOR.inc("batch_429_total")
                return index, "__RATE_LIMITED__"
            return index, None

        content = str(result.get("content") or "").strip()
        if not content:
            return index, None

        MONITOR.inc("batch_item_ok_total")
        return index, content


# ── Context helpers ──

_GEO_FACT_KEYS = frozenset({"city", "country", "currency", "location", "region"})
_ABSTRACT_ITEM_MARKERS = (
    "тессеракт", "куб", "грань", "грани", "измерен", "геометр", "математ",
    "теорем", "уравнен", "докаж", "логик", "физик", "квант", "атом",
    "молекул", "днк", "иммун", "бактер", "фотосинтез", "блокчейн",
    "тёмная материя", "темная материя", "чёрн", "черн", "дыр",
)
_PERSONAL_ITEM_MARKERS = (
    "погод", "мой город", "у меня", "мне ", "моя ", "мой ", "мои ",
    "из минска", "в минск", "беларус", "поездк", "маршрут",
)


def _item_needs_user_facts(item: str) -> bool:
    """Гео/личные факты только если пункт явно про пользователя или быт."""
    low = (item or "").strip().lower()
    if not low:
        return False
    if any(m in low for m in _PERSONAL_ITEM_MARKERS):
        return True
    if any(m in low for m in _ABSTRACT_ITEM_MARKERS):
        return False
    if "?" in low and not any(m in low for m in ("я ", "мой", "мне", "моя")):
        return False
    return True


def _build_user_facts_context(user_facts: Dict[str, Any], *, item: str = "") -> str:
    """Собрать user_facts в минимальный текстовый блок (с фильтром по пункту)."""
    if not isinstance(user_facts, dict):
        return ""
    if item and not _item_needs_user_facts(item):
        return ""
    try:
        parts = []
        for k, v in user_facts.items():
            if isinstance(v, str) and v.strip():
                parts.append(f"{k}: {v.strip()[:100]}")
            elif isinstance(v, (int, float)):
                parts.append(f"{k}: {v}")
        if parts:
            return "Факты о пользователе: " + "; ".join(parts)
    except Exception as e:
        logger.debug('%s optional failed: %s', 'batch_processor', e, exc_info=True)
    return ""


# ── Главная функция ──

async def run_parallel_batch(
    items: List[str],
    user_id: str,
    user_facts: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Выполнить batch пунктов параллельно.

    Args:
        items: список пунктов
        user_id: ID пользователя
        user_facts: словарь user_facts (опционально)

    Returns:
        Dict с ключами:
          - ok: bool
          - reply: str (склеенный ответ)
          - mode: "parallel" | "sequential"
          - answered: int
          - total: int
          - errors: int
          - pending_items: list[str] (неотвеченные пункты)
    """
    if not _PARALLEL_ENABLED:
        return {"ok": False, "reply": "", "mode": "sequential",
                "answered": 0, "total": len(items), "errors": 0,
                "pending_items": items}

    total = len(items)
    if total < _MIN_ITEMS_FOR_PARALLEL:
        return {"ok": False, "reply": "", "mode": "sequential",
                "answered": 0, "total": total, "errors": 0,
                "pending_items": items}

    if not is_parallel_eligible(items):
        logger.info("[batch_processor] items not eligible for parallel")
        return {"ok": False, "reply": "", "mode": "sequential",
                "answered": 0, "total": total, "errors": 0,
                "pending_items": items}

    _refresh_semaphore()

    try:
        from core.calendar_facts import build_calendar_date_hint_for_llm
        calendar_hint = build_calendar_date_hint_for_llm(user_id) or ""
    except Exception:
        calendar_hint = ""

    try:
        from core.timezone_inference import format_clock_hint_for_llm, infer_timezone_from_facts
        tz = infer_timezone_from_facts(user_facts or {})
        time_hint = format_clock_hint_for_llm(tz) or ""
    except Exception:
        time_hint = ""

    profile_hint = (
        "Профиль: batch. Отвечай кратко, максимально информативно. "
        "Не используй инструменты — только текст."
    )

    max_parallel = _get_max_parallel()
    logger.info("[batch_processor] starting parallel batch: %d items, max_parallel=%d",
                total, max_parallel)

    coros = [
        _run_single_item(
            item,
            i + 1,
            total,
            user_id,
            _build_user_facts_context(user_facts or {}, item=item),
            time_hint,
            calendar_hint,
            profile_hint,
        )
        for i, item in enumerate(items)
    ]

    t_start = time.monotonic()
    results = await asyncio.gather(*coros, return_exceptions=True)
    elapsed = time.monotonic() - t_start

    answers: Dict[int, str] = {}
    errors = 0
    rate_limited = 0

    for res in results:
        if isinstance(res, Exception):
            errors += 1
            logger.warning("[batch_processor] gather exception: %s", res)
            continue
        if not isinstance(res, tuple) or len(res) != 2:
            errors += 1
            continue
        idx, content = res
        if content is None:
            errors += 1
        elif content == "__RATE_LIMITED__":
            rate_limited += 1
            errors += 1
        else:
            answers[idx] = content

    _record_batch_result(errors, rate_limited, total)

    answered = len(answers)
    elapsed_ms = elapsed * 1000.0
    MONITOR.inc("batch_parallel_total")
    MONITOR.inc("batch_parallel_answered", answered)
    MONITOR.inc("batch_parallel_errors", errors)

    pending_items = [items[i] for i in range(total) if (i + 1) not in answers]

    _avg_answer_len = (
        sum(len(v) for v in answers.values()) / answered if answered else 0.0
    )
    if (
        answered == total
        and total >= _MIN_ITEMS_FOR_PARALLEL
        and elapsed_ms < _MIN_ELAPSED_MS
        and _avg_answer_len < 12
    ):
        logger.warning(
            "[batch_processor] suspiciously fast batch (%.0fms, avg_len=%.0f for %d items) — "
            "likely stale cache; fallthrough to sequential",
            elapsed_ms,
            _avg_answer_len,
            total,
        )
        MONITOR.inc("batch_parallel_fast_reject_total")
        return {"ok": False, "reply": "", "mode": "sequential",
                "answered": 0, "total": total, "errors": 0,
                "latency_ms": elapsed_ms, "pending_items": items}

    if not answers:
        logger.warning("[batch_processor] all items failed, fallback to sequential")
        return {"ok": False, "reply": "", "mode": "sequential",
                "answered": 0, "total": total, "errors": errors,
                "latency_ms": elapsed * 1000, "pending_items": items}

    lines: List[str] = []
    for idx in sorted(answers.keys()):
        lines.append(f"{idx}. {answers[idx]}")

    if errors > 0:
        lines.append("")
        lines.append(f"[Не удалось обработать {errors} из {total} пунктов.]")

    reply = "\n\n".join(lines)

    logger.info(
        "[batch_processor] completed: %d/%d answered, %d errors, %d rate-limited, %.0fms, max_parallel=%d",
        answered, total, errors, rate_limited, elapsed_ms, max_parallel,
    )

    return {
        "ok": True,
        "reply": reply,
        "mode": "parallel",
        "answered": answered,
        "total": total,
        "errors": errors,
        "rate_limited": rate_limited,
        "latency_ms": elapsed * 1000,
        "pending_items": pending_items,
    }