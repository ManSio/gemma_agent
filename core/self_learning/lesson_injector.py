"""Lesson Injector — inserts relevant past lessons into the agent's context."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import List, Tuple

from core.self_learning.lesson_manager import LessonManager
from core.self_learning.models import Lesson

logger = logging.getLogger(__name__)


def _get_manager() -> LessonManager:
    return LessonManager.get_instance()


def _max_injected() -> int:
    try:
        return max(0, int(os.getenv("SELF_LEARNING_MAX_INJECTED_LESSONS", "3").strip()))
    except (ValueError, TypeError):
        return 3


async def build_lessons_hint(user_text: str, max_lessons: int | None = None) -> Tuple[str, List[Lesson]]:
    """Build a formatted hint block with the most relevant past lessons.

    Returns (hint_string, list_of_injected_lessons).
    hint_string is empty if no relevant lessons found.
    """
    if max_lessons is None:
        max_lessons = _max_injected()
    if max_lessons <= 0:
        return "", []

    q = (user_text or "").strip()
    if not q:
        return "", []

    try:
        mgr = _get_manager()
        lessons = await mgr.find_relevant_lessons(q, top_k=max_lessons)
    except Exception:
        logger.debug("[self_learning] lesson injection error", exc_info=True)
        return "", []

    if not lessons:
        return "", []

    # Bump access count
    now = datetime.now(timezone.utc).isoformat()
    for lesson in lessons:
        lesson.access_count += 1
        lesson.last_accessed_at = now
        try:
            mgr.update_lesson(lesson)
        except Exception as e:
            logger.debug('%s optional failed: %s', 'lesson_injector', e, exc_info=True)
    lines: List[str] = ["[Self-Learning System - Past Lessons]:"]
    for i, lesson in enumerate(lessons, 1):
        lines.append(f"- Lesson {i}: {lesson.content}")

    return "\n".join(lines), lessons
