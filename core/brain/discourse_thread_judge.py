"""LLM-судья нити диалога: STAY / BRANCH / CORRECT на пограничных ходах."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, Optional

from core.brain.env import env_flag
from core.monitoring import MONITOR

logger = logging.getLogger(__name__)

ACTION_STAY = "stay"
ACTION_BRANCH = "branch"
ACTION_CORRECT = "correct"


def thread_judge_enabled() -> bool:
    """Включён ли LLM thread judge."""
    return env_flag("DISCOURSE_THREAD_JUDGE_ENABLED", default=True)


def _judge_model() -> str:
    """Модель для thread judge."""
    return (
        os.getenv("DISCOURSE_THREAD_JUDGE_MODEL")
        or os.getenv("ROUTER_LLM_MODEL")
        or "liquid/lfm-2.5-1.2b-instruct:free"
    ).strip()


def _judge_timeout_sec() -> float:
    """Таймаут вызова thread judge."""
    try:
        return max(2.0, float(os.getenv("DISCOURSE_THREAD_JUDGE_TIMEOUT_SEC", "8")))
    except ValueError:
        return 8.0


def _judge_min_confidence() -> float:
    """Минимальная уверенность для применения решения judge."""
    try:
        return max(0.0, min(1.0, float(os.getenv("DISCOURSE_THREAD_JUDGE_MIN_CONFIDENCE", "0.55"))))
    except ValueError:
        return 0.55


def _judge_system_prompt() -> str:
    """Системный промпт thread judge."""
    return (
        "Classify how the latest user message relates to the dialogue thread. "
        "Reply with ONLY valid JSON.\n"
        "thread_action: stay | branch | correct\n"
        "- stay: elliptical follow-up on the SAME topic as last Q/A\n"
        "- branch: new topic or standalone question\n"
        "- correct: user rejects previous answer or redirects topic\n"
        "confidence: 0.0-1.0\n"
        "resolved_user_text: optional standalone rewrite if stay/correct (same language)\n"
        "topic_summary: optional 3-8 words for active topic"
    )


def _parse_judge_response(raw: str) -> Optional[Dict[str, Any]]:
    """Разобрать JSON ответ thread judge."""
    text = (raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    action = str(data.get("thread_action") or data.get("action") or "").strip().lower()
    if action not in {ACTION_STAY, ACTION_BRANCH, ACTION_CORRECT}:
        return None
    try:
        conf = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    out: Dict[str, Any] = {
        "thread_action": action,
        "confidence": max(0.0, min(1.0, conf)),
        "source": "llm",
    }
    resolved = str(data.get("resolved_user_text") or data.get("resolved_text") or "").strip()
    if resolved:
        out["resolved_user_text"] = resolved[:500]
    topic = str(data.get("topic_summary") or data.get("topic") or "").strip()
    if topic:
        out["topic_summary"] = topic[:120]
    return out


async def judge_thread_async(
    llm: Any,
    user_text: str,
    context: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Спросить LLM: продолжение нити, новая тема или коррекция."""
    if not thread_judge_enabled():
        return None
    if llm is None:
        return None
    ut = (user_text or "").strip()
    if not ut:
        return None

    dsv_line = ""
    last_q = ""
    last_a = ""
    try:
        from core.brain.dialogue_context import build_dsv
        from core.dialogue_recheck_anchor import last_qa_pair

        ctx = context if isinstance(context, dict) else {}
        ctx = {**ctx, "user_text": ut}
        dsv = build_dsv(ctx)
        dsv_line = dsv.to_prompt()
        pair = last_qa_pair(ctx.get("recent_dialogue") or ctx.get("recent_messages"))
        if pair:
            last_q, last_a = pair[0][:240], pair[1][:240]
    except Exception as e:
        logger.debug("thread_judge context: %s", e)

    prompt = (
        f"User: {ut[:400]}\n"
        f"Dialogue: {dsv_line}\n"
        f"Last_user: {last_q}\n"
        f"Last_assistant: {last_a}\n"
        "JSON:"
    )
    try:
        result = await asyncio.wait_for(
            llm.generate(
                prompt=prompt,
                model=_judge_model(),
                system_prompt=_judge_system_prompt(),
                max_tokens=180,
                temperature=0.05,
                telemetry_kind="discourse_judge",
                telemetry_tag="discourse_thread_judge",
            ),
            timeout=_judge_timeout_sec(),
        )
    except asyncio.TimeoutError:
        MONITOR.inc("discourse_judge_timeout_total")
        logger.warning("[discourse_judge] timeout")
        return None
    except Exception as e:
        MONITOR.inc("discourse_judge_error_total")
        logger.warning("[discourse_judge] error: %s", e)
        return None

    if isinstance(result, dict) and result.get("error"):
        MONITOR.inc("discourse_judge_error_total")
        return None
    content = str((result or {}).get("content") or "").strip()
    parsed = _parse_judge_response(content)
    if not parsed:
        MONITOR.inc("discourse_judge_parse_fail_total")
        return None
    if float(parsed.get("confidence") or 0.0) < _judge_min_confidence():
        MONITOR.inc("discourse_judge_low_conf_total")
        return None
    MONITOR.inc("discourse_judge_ok_total")
    return parsed
