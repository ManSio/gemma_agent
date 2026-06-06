"""SelfLearningEngine — closed-loop self-improvement from self-verify failures."""
from __future__ import annotations

from core.self_learning.models import Lesson
from core.self_learning.lesson_manager import LessonManager
from core.self_learning.reflexion import reflect_on_error
from core.self_learning.lesson_injector import build_lessons_hint
from core.self_learning.lesson_validator import validate_lessons_against_response, consolidate_and_retire

__all__ = [
    "Lesson",
    "LessonManager",
    "reflect_on_error",
    "build_lessons_hint",
    "validate_lessons_against_response",
    "consolidate_and_retire",
]
