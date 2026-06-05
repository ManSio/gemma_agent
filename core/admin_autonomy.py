"""Сводка автономности системы: уроки, эталоны, классификатор, sanitizer.

Команда /admin_autonomy собирает:
- Количество активных уроков reflexion (из self_learning_lessons.jsonl)
- Количество эталонов в Qdrant (коллекция task_profiles)
- Статистика классификатора: hits / misses / errors
- Cache hit ratio за rolling-окно (из llm_usage_store + MONITOR)
- Количество срабатываний sanitizer'а (удалённых сообщений из истории)
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from core.monitoring import MONITOR

logger = logging.getLogger(__name__)


def _active_lessons_count() -> int:
    """Количество уроков со статусом 'active'."""
    try:
        from core.self_learning.lesson_manager import LessonManager

        lm = LessonManager.get_instance()
        lessons = lm.load_active_lessons()
        return len(lessons)
    except Exception as e:
        logger.debug("[admin_autonomy] lessons count error: %s", e)
        return -1


def _etalon_count() -> int:
    """Количество точек в Qdrant-коллекции task_profiles."""
    try:
        from core.brain.classifier import COLLECTION, _get_client, _qdrant_configured

        if not _qdrant_configured():
            return -1
        client = _get_client()
        if client is None:
            return -1
        result = client.scroll(COLLECTION, limit=1)
        if not isinstance(result, dict):
            return -1
        points = result.get("points", [])
        if not points:
            return 0
        # Если вернулась 1 точка, значит есть как минимум 1;
        # если есть next_page_offset — точек больше. Для точного счёта
        # используем коллекцию + scroll, но для простоты — scroll всех.
        total = 0
        offset: int | None = None
        while True:
            batch = client.scroll(COLLECTION, limit=100, offset=offset)
            if not isinstance(batch, dict):
                break
            pts = batch.get("points", [])
            total += len(pts)
            nxt = batch.get("next_page_offset")
            if nxt is None:
                break
            offset = int(nxt)
        return total
    except Exception as e:
        logger.debug("[admin_autonomy] etalon count error: %s", e)
        return -1


def _classifier_stats() -> Dict[str, int]:
    """Классификатор: hits / misses / errors из MONITOR."""
    snap = MONITOR.snapshot()
    counters: Dict[str, int] = snap.get("counters", {}) if isinstance(snap, dict) else {}
    return {
        "hits": int(counters.get("brain_classifier_etalon_hit_total", 0)),
        "misses": int(counters.get("brain_classifier_etalon_miss_total", 0)),
        "errors": int(counters.get("brain_classifier_error_total", 0)),
    }


def _sanitizer_stats() -> int:
    """Счётчик sanitizer'а."""
    try:
        from core.brain.cot_strip import sanitize_counter

        return sanitize_counter()
    except Exception:
        return -1


def _cache_stats() -> Dict[str, Any]:
    """Cache hit ratio / coverage за rolling-окно (последние 24h)."""
    try:
        from core.llm_usage_store import recent_rows

        rows = recent_rows(days=1.0)
        if not rows:
            return {}
        window = rows[:20]
        cached_sum = 0
        prompt_sum = 0
        hits = 0
        total = 0
        for r in window:
            cpt = r.get("cached_prompt_tokens")
            pt = r.get("prompt_tokens")
            if isinstance(cpt, (int, float)) and isinstance(pt, (int, float)) and pt > 0:
                cached_sum += int(cpt)
                prompt_sum += int(pt)
                if cpt > 0:
                    hits += 1
                total += 1
        coverage = round(cached_sum / max(prompt_sum, 1) * 100, 1)
        hit_rate = round(hits / max(total, 1) * 100, 1)
        return {
            "cache_coverage_pct": coverage,
            "hit_rate_pct": hit_rate,
            "rolling_rows": total,
            "cached_tok_sum": cached_sum,
            "prompt_tok_sum": prompt_sum,
        }
    except Exception as e:
        logger.debug("[admin_autonomy] cache stats error: %s", e)
        return {}


def build_autonomy_report() -> Dict[str, Any]:
    """Собирает полную сводку автономности."""
    snap = MONITOR.snapshot() if callable(getattr(MONITOR, "snapshot", None)) else {}
    return {
        "reflexion_lessons_active": _active_lessons_count(),
        "qdrant_etalons_count": _etalon_count(),
        "classifier": _classifier_stats(),
        "sanitizer_removed_total": _sanitizer_stats(),
        "cache": _cache_stats(),
        "uptime_hint_sec": snap.get("uptime_hint_sec", 0) if isinstance(snap, dict) else 0,
    }
