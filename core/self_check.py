"""
Lightweight self-check: verifies answer quality without long context.
If the answer is normal — returns it as-is.
Uses minimal token budget (~200 prompt tokens).
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

SELF_CHECK_VERSION = "1.1.0"

SELF_CHECK_SYSTEM = """\
Ты — проверяющий качество ответа ассистента.
Если ответ нормальный — верни "ok".
Если есть проблема — верни "fix: <что исправить>".
Будь краток.
Используй факты внизу (дата, время, имя) — не гадай."""

SELF_CHECK_PROMPT = """\
{grounding}

Вопрос пользователя: {user_text}

Ответ ассистента:
{answer}

Проверь:
1. Ответ по теме?
2. Нет бреда/галлюцинаций?
3. Нет повторов по кругу?

Верни "ok" или "fix: <описание>".
Не добавляй ничего лишнего."""


async def self_check_answer(
    llm_call: Callable[..., Any],
    *,
    user_text: str,
    answer: str,
    intent: Optional[str] = None,
    user_name: str = "",
    clock_info: str = "",
) -> str:
    """
    Light self-check. Returns the original answer if OK,
    otherwise returns a fixed version.

    llm_call: async callable(prompt, system_prompt, max_tokens, temperature)
              returning dict with key "text".

    intent: if "direct_action" or "direct_tool_action" — skip self-check (softest path).
    user_name: имя пользователя (из user_facts), если известно.
    clock_info: строка с текущей датой/временем.
    """
    if not answer or not answer.strip():
        return answer

    # direct_action / direct_tool_action: максимально мягкая проверка — не переписывать
    if intent in ("direct_action", "direct_tool_action"):
        if len(answer.strip()) < 8:
            return answer
        # Only skip, never rewrite direct action / tool answers
        return answer

    # Fast skip: very short answers are usually fine
    if len(answer.strip()) < 15:
        return answer

    # Skip if answer already passed through self-check (avoid loops)
    user_text = (user_text or "").strip()
    if not user_text:
        return answer

    # Build grounding block with real facts
    parts_ground = []
    if clock_info:
        parts_ground.append(f"Реальное время: {clock_info}")
    else:
        from datetime import datetime, timezone
        parts_ground.append(
            f"Реальное время: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )
    if user_name:
        parts_ground.append(f"Имя пользователя: {user_name}")
    else:
        parts_ground.append("Имя пользователя: неизвестно")
    grounding_block = "; ".join(parts_ground)

    prompt = SELF_CHECK_PROMPT.format(
        grounding=grounding_block,
        user_text=user_text[:600],
        answer=answer[:1200],
    )

    try:
        result = await llm_call(
            prompt=prompt,
            system_prompt=SELF_CHECK_SYSTEM,
            max_tokens=80,
            temperature=0.0,
        )
    except Exception as e:
        logger.debug("self_check_answer llm_call failed: %s", e)
        return answer

    verdict = str(result.get("text") or result.get("content") or "").strip()

    if verdict.lower().startswith("ok"):
        return answer
    if verdict.lower().startswith("fix:"):
        fix_note = verdict[4:].strip()
        return f"{answer}\n\n[перепроверено: {fix_note}]"

    return answer
