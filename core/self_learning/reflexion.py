"""Reflexion Module — generates Lessons from rejected self-verify fixes.

Attempts LLM-based reflection (fast model), with heuristic fallback.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

from core.self_learning.models import Lesson

logger = logging.getLogger(__name__)

_REFLEXION_PROMPT = """Ты — аналитик, который учит ассистента на его ошибках.

Пользователь задал вопрос. Ассистент дал ответ. Система самопроверки
предложила исправление, но оно оказалось некачественным.

Твоя задача: сформулировать ОДИН короткий урок — правило на русском языке,
которое поможет ассистенту в будущем не допускать подобных ошибок.

Формат: одна строка, 1–2 предложения, без пояснений.
Начинай с «Если» или «При».

Примеры:
- Если вопрос о физическом явлении, объясни механизм, а не просто назови факт.
- При запросе о цвете предмета укажи научную причину (длина волны, рассеяние).
- Если пользователь просит сравнение, приведи конкретные численные параметры.
"""


def _reflection_model() -> str:
    return os.getenv("SELF_LEARNING_REFLECTION_MODEL", "meta-llama/llama-3.1-8b-instruct").strip()


def _rejection_to_category(reason: str) -> str:
    mapping: Dict[str, str] = {
        "quality_check": "factual_error",
        "fix_too_short": "incomplete_answer",
        "fix_too_long": "verbose_fix",
    }
    return mapping.get(reason, "general")


def _extract_tags(user_text: str, limit: int = 5) -> List[str]:
    txt = (user_text or "").strip().lower()
    if not txt:
        return []
    # Simple word-frequency tag extraction (Russian stopwords skipped)
    stopwords = {
        "что", "это", "как", "для", "если", "при", "или", "почему",
        "зачем", "когда", "где", "кто", "чем", "чтобы", "можно",
        "есть", "быть", "был", "была", "было", "были", "нет", "да",
        "ещё", "уже", "все", "его", "её", "их", "мне", "я", "ты",
        "он", "она", "оно", "мы", "вы", "они", "мой", "твой", "наш",
        "ваш", "себя", "просто", "очень", "тоже", "только",
    }
    words = [w for w in txt.split() if len(w) > 2 and w.lower() not in stopwords]
    from collections import Counter
    freq = Counter(words)
    return [w for w, _ in freq.most_common(limit)]


def _reflect_heuristic(user_text: str, original_reply: str, bad_fix: str) -> str:
    """Heuristic lesson generation when LLM is unavailable."""
    txt = (user_text or "").strip().lower()
    prefixes: Dict[str, str] = {
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
    profile: str = "standard",
    model: str = "",
    self_verify_model: str = "",
    rejection_reason: str = "quality_check",
    llm: Any = None,
) -> Optional[Lesson]:
    """Generate a Lesson from a rejected self-verify fix.

    Returns None if generation fails entirely.
    """
    logger.info("[reflexion] reflect_on_error called: user_text=%s... rejection=%s", user_text[:80], rejection_reason)
    content = ""
    if llm is not None:
        try:
            prompt = (
                f"Запрос пользователя: {user_text}\n"
                f"Ответ ассистента: {original_reply[:500]}\n"
                f"Некачественное исправление: {bad_fix[:300]}\n\n"
                f"Сформулируй урок для ассистента."
            )
            reflection_model = _reflection_model()
            logger.info("[reflexion] calling LLM model=%s", reflection_model)
            result = await asyncio.wait_for(
                llm.generate(
                    prompt=prompt,
                    system_prompt=_REFLEXION_PROMPT,
                    model=reflection_model,
                    max_tokens=120,
                    temperature=0.4,
                ),
                timeout=10.0,
            )
            content = str(result.get("content", "") or "").strip()
            logger.info("[reflexion] LLM returned content len=%d: %.100s", len(content), content)
            if content and len(content) < 10:
                logger.info("[reflexion] content too short (<10 chars), clearing")
                content = ""
        except asyncio.TimeoutError:
            logger.warning("[reflexion] LLM timeout, using heuristic")
        except Exception as e:
            logger.warning("[reflexion] LLM error: %s", e)

    if not content:
        content = _reflect_heuristic(user_text, original_reply, bad_fix)
        logger.info("[reflexion] heuristic generated content len=%d: %.100s", len(content), content)

    if not content:
        logger.warning("[reflexion] no content from LLM or heuristic, returning None")
        return None

    source_context = {
        "user_text": user_text[:500],
        "original_reply": original_reply[:500],
        "bad_fix": bad_fix[:300],
        "profile": profile,
        "model": model,
        "self_verify_model": self_verify_model,
        "rejection_reason": rejection_reason,
    }
    category = _rejection_to_category(rejection_reason)
    tags = _extract_tags(user_text)

    lesson = Lesson.new(
        content=content,
        source="reflexion",
        source_context=source_context,
        tags=tags,
        category=category,
    )
    logger.info("[reflexion] lesson generated id=%s content=%.120s", lesson.id, lesson.content)
    return lesson
