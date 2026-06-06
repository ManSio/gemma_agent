"""Lesson Validator — feedback loop that adjusts effectiveness scores and triggers consolidation.

Scoring:
- self_verify ok → +0.1 (lesson helped)
- self_verify fix accepted → -0.3 (lesson failed to prevent error)
- irrelevant → no change
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, List

from core.self_learning.lesson_manager import LessonManager, _retire_score, _consolidate_score
from core.self_learning.models import Lesson

logger = logging.getLogger(__name__)


def _get_manager() -> LessonManager:
    return LessonManager.get_instance()


async def validate_lessons_against_response(
    injected_lessons: List[Lesson],
    assistant_reply: str,
    self_verify_result: str,
) -> None:
    """Adjust effectiveness scores of injected lessons based on self_verify outcome.

    Args:
        injected_lessons: The lessons that were injected into this request's context.
        assistant_reply: The final reply sent to the user.
        self_verify_result: The raw self_verify output ("ok" or "fix: ...").
    """
    if not injected_lessons:
        return

    mgr = _get_manager()
    ver = (self_verify_result or "").strip()
    is_ok = ver == "ok" or ver.lower() == "ok"
    is_fix = ver.lower().startswith("fix:")

    for lesson in injected_lessons:
        if lesson.status != "active":
            continue
        if is_ok:
            lesson.effectiveness_score = min(1.0, lesson.effectiveness_score + 0.1)
        elif is_fix:
            lesson.effectiveness_score = max(0.0, lesson.effectiveness_score - 0.3)
        try:
            mgr.update_lesson(lesson)
        except Exception as e:
            logger.debug('%s optional failed: %s', 'lesson_validator', e, exc_info=True)
async def consolidate_and_retire(llm: Any = None) -> int:
    """Run consolidation/retirement pass on all active lessons.

    - Retires lessons with effectiveness_score below RETIRE_SCORE.
    - Consolidates lessons with effectiveness_score above CONSOLIDATE_SCORE.

    Returns the number of changes made.
    """
    mgr = _get_manager()
    active = mgr.load_active_lessons()
    if not active:
        return 0

    retire_threshold = _retire_score()
    consolidate_threshold = _consolidate_score()

    changes = 0
    to_retire: List[Lesson] = []
    to_consolidate: List[Lesson] = []
    remaining: List[Lesson] = []

    for lesson in active:
        if lesson.effectiveness_score < retire_threshold:
            to_retire.append(lesson)
        elif lesson.effectiveness_score > consolidate_threshold:
            to_consolidate.append(lesson)
        else:
            remaining.append(lesson)

    # Retire low-score lessons
    for lesson in to_retire:
        lesson.status = "retired"
        try:
            mgr.update_lesson(lesson)
            changes += 1
        except Exception as e:
            logger.debug('%s optional failed: %s', 'lesson_validator', e, exc_info=True)
    if to_retire:
        logger.info("[self_learning] retired %d low-effectiveness lessons", len(to_retire))

    # Consolidate high-score lessons
    if len(to_consolidate) >= 2:
        changes += await _consolidate_lessons(to_consolidate, mgr, llm)

    return changes


async def _consolidate_lessons(lessons: List[Lesson], mgr: LessonManager, llm: Any = None) -> int:
    """Merge multiple high-score lessons into one general lesson via LLM."""
    if len(lessons) < 2:
        return 0

    content_lines = [f"{i+1}. {l.content}" for i, l in enumerate(lessons)]
    source_text = "\n".join(content_lines)

    if llm is not None:
        try:
            import asyncio

            prompt = (
                f"Ниже перечислены несколько уроков, которые ассистент усвоил.\n"
                f"Объедини их в ОДИН общий урок-правило на русском языке.\n"
                f"Формат: одна строка, 1–2 предложения. Начинай с «Если» или «При».\n\n"
                f"Уроки:\n{source_text}"
            )
            consolidation_model = os.getenv(
                "SELF_LEARNING_REFLECTION_MODEL", "meta-llama/llama-3.1-8b-instruct"
            ).strip()
            result = await asyncio.wait_for(
                llm.generate(
                    prompt=prompt,
                    system_prompt="Ты — аналитик, обобщающий правила поведения.",
                    model=consolidation_model,
                    max_tokens=150,
                    temperature=0.3,
                ),
                timeout=10.0,
            )
            consolidated_content = str(result.get("content", "") or "").strip()
        except (asyncio.TimeoutError, Exception):
            consolidated_content = ""
    else:
        consolidated_content = ""

    if not consolidated_content or len(consolidated_content) < 10:
        consolidated_content = lessons[0].content

    # Collect tags from all sources
    all_tags: List[str] = []
    for l in lessons:
        all_tags.extend(l.tags)
    unique_tags = list(dict.fromkeys(all_tags))[:5]

    consolidated = Lesson.new(
        content=consolidated_content,
        source="consolidation",
        source_context={"consolidated_from": [l.id for l in lessons]},
        tags=unique_tags,
        category=lessons[0].category,
    )
    consolidated.effectiveness_score = sum(l.effectiveness_score for l in lessons) / len(lessons)
    consolidated.strength = max(l.strength for l in lessons)

    await mgr.store_lesson(consolidated)

    for lesson in lessons:
        lesson.status = "consolidated"
        try:
            mgr.update_lesson(lesson)
        except Exception as e:
            logger.debug('%s optional failed: %s', 'lesson_validator', e, exc_info=True)
    logger.info("[self_learning] consolidated %d lessons into %s", len(lessons), consolidated.id)
    return len(lessons) + 1
