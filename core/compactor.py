"""
LLM Compactor — заменяет тупую обрезку диалога/документов на LLM-суммаризацию.

Когда контекст превышает бюджет (collapse_level >= 3 или est_tokens > limit),
вместо обрезания первых/последних N символов отдаёт блоки на сжатие через
дешёвую модель (free tier). При отказе LLM — прозрачный fallback на старую обрезку.

protect_last_n: последние N сообщений НЕ сжимаются (остаются verbatim).
  По умолчанию 2 (текущий user + assistant ход).
  Настраивается через COMPACTOR_PROTECT_LAST_N (env) или конфиг.

Полностью опционален: все фичи управляются через token_efficiency.yml.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from core.token_efficiency import (
    compactor_enabled,
    compactor_threshold,
    compactor_max_summary_tokens,
)

logger = logging.getLogger(__name__)

COMPACTOR_VERSION = "1.2.0"

# ── protect_last_n ──


def _protect_last_n() -> int:
    """
    Сколько ПОСЛЕДНИХ сообщений НЕ сжимать через LLM-компрессию.
    По умолчанию 2: текущий ход (user+assistant) остаётся полным.
    """
    try:
        return max(0, int(os.getenv("COMPACTOR_PROTECT_LAST_N", "2").strip()))
    except (TypeError, ValueError):
        return 2


# ── Синхронные проверки (без LLM, без await) ──


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    try:
        return max(minimum, int((os.getenv(name) or "").strip() or str(default)))
    except (TypeError, ValueError):
        return max(minimum, default)


def compaction_budget_tokens() -> int:
    """Единый бюджет для compactor: budget_hard_limit, не collapse_max (8k) когда collapse off."""
    raw = (os.getenv("COMPACTOR_BUDGET_TOKENS") or "").strip()
    if raw:
        try:
            return max(100, int(raw))
        except ValueError:
            pass
    from core.token_efficiency import (
        budget_enabled,
        budget_hard_limit_tokens,
        collapse_enabled,
        collapse_max_prompt_tokens,
    )

    if budget_enabled():
        return budget_hard_limit_tokens()
    if collapse_enabled():
        return collapse_max_prompt_tokens()
    return budget_hard_limit_tokens()


def compactor_turn_limit() -> int:
    return _env_int("COMPACTOR_TURN_LIMIT", 8, minimum=1)


def compactor_min_dialogue_messages() -> int:
    return _env_int("COMPACTOR_MIN_DIALOGUE_MESSAGES", 4, minimum=2)


def _count_dialogue_turns(messages: List[Any]) -> int:
    n = 0
    for i, m in enumerate(messages):
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "").lower()
        if role == "user":
            n += 1
        elif role == "assistant":
            is_last = i == len(messages) - 1
            next_is_user = (
                i + 1 < len(messages)
                and str(messages[i + 1].get("role") or "").lower() == "user"
            )
            if is_last or next_is_user:
                n += 1
    return n


def evaluate_compaction_triggers(
    *,
    collapse_level: int,
    est_tokens: int,
    dialogue_messages: Optional[List[Any]] = None,
    turn_index: int = 0,
) -> tuple[bool, Dict[str, Any]]:
    """
    Dual-trigger: prompt token pressure OR session turn_index OR dialogue half-budget.
    Возвращает (needed, meta) для turns.jsonl / compaction log (фаза 0).
    """
    meta: Dict[str, Any] = {
        "compactor_version": COMPACTOR_VERSION,
        "needed": False,
        "compacted": False,
        "triggers": [],
    }
    if not compactor_enabled():
        meta["reason"] = "compactor_disabled"
        return False, meta

    budget = compaction_budget_tokens()
    threshold = compactor_threshold()
    est = max(0, int(est_tokens or 0))
    turn_idx = max(0, int(turn_index or 0))
    meta.update(
        {
            "budget_tokens": budget,
            "threshold": threshold,
            "est_tokens_before": est,
            "turn_index": turn_idx,
            "turn_limit": compactor_turn_limit(),
            "min_dialogue_messages": compactor_min_dialogue_messages(),
        }
    )
    if budget <= 0:
        meta["reason"] = "budget_zero"
        return False, meta

    dialogue_tokens = 0
    dialogue_turns = 0
    msg_count = 0
    if isinstance(dialogue_messages, list):
        msg_count = len(dialogue_messages)
        from core.brain.prompt_pack import estimate_tokens_approx

        parts = [
            str(m.get("text") or m.get("content") or "")
            for m in dialogue_messages
            if isinstance(m, dict)
        ]
        dialogue_tokens = estimate_tokens_approx("\n".join(parts))
        dialogue_turns = _count_dialogue_turns(dialogue_messages)
    meta["dialogue_msgs"] = msg_count
    meta["dialogue_tokens_est"] = dialogue_tokens
    meta["dialogue_turns"] = dialogue_turns

    triggers: List[str] = []
    if int(collapse_level or 0) >= 3:
        triggers.append("collapse_level")
    token_limit = int(budget * threshold) if budget > 0 and 0 < threshold < 1.0 else 0
    if token_limit and est > token_limit:
        triggers.append("prompt_token_pressure")
    if turn_idx > compactor_turn_limit():
        triggers.append("session_turn_index")
    half_budget = int(budget * 0.5) if budget > 0 else 0
    if msg_count >= compactor_min_dialogue_messages() and half_budget and dialogue_tokens > half_budget:
        triggers.append("dialogue_half_budget")

    meta["triggers"] = triggers
    meta["needed"] = bool(triggers)
    return bool(triggers), meta


def compaction_needed(
    collapse_level: int,
    est_tokens: int,
    max_budget: int,
    *,
    turn_index: int = 0,
    dialogue_messages: Optional[List[Any]] = None,
) -> bool:
    """
    Нужна ли LLM-компактификация? (обёртка над evaluate_compaction_triggers).

    max_budget оставлен для совместимости тестов; при evaluate используется compaction_budget_tokens().
    """
    _ = max_budget
    needed, _meta = evaluate_compaction_triggers(
        collapse_level=collapse_level,
        est_tokens=est_tokens,
        dialogue_messages=dialogue_messages,
        turn_index=turn_index,
    )
    return needed


def build_compaction_log(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Узкий снимок для turns.jsonl."""
    if not isinstance(meta, dict):
        return {}
    keys = (
        "needed",
        "compacted",
        "triggers",
        "reason",
        "budget_tokens",
        "threshold",
        "est_tokens_before",
        "est_tokens_after",
        "turn_index",
        "dialogue_msgs",
        "dialogue_tokens_est",
        "dialogue_summary_len",
        "dialogue_protected_count",
        "document_summary_len",
    )
    return {k: meta[k] for k in keys if k in meta and meta[k] is not None and meta[k] != ""}


# ── Промпты для LLM-суммаризации ──

_DIALOGUE_COMPACT_PROMPT = (
    "Ты — компактор диалога. Сократи переписку до 2–3 предложений, "
    "сохранив: о чём спрашивал пользователь, какие ответы даны, "
    "ключевые факты и договорённости. Только факты, без оценок.\n\n"
    "{body}\n\n"
    "Краткая сводка:"
)

_DOCUMENT_COMPACT_PROMPT = (
    "Сократи следующий текст до 2–3 предложений, сохранив ключевую информацию. "
    "Только суть, без лишних деталей.\n\n"
    "{body}\n\n"
    "Краткая сводка:"
)


async def compact_dialogue_llm(
    llm: Any,
    messages: List[Any],
    max_tokens: int = 0,
    protect_last_n: int | None = None,
) -> tuple[str, list[dict]]:
    """
    Асинхронная LLM-суммаризация списка сообщений диалога.

    protect_last_n: последние N сообщений НЕ отправляются на сжатие,
      а возвращаются verbatim как второй элемент кортежа (protected_messages).

    Returns:
      (summary, protected_messages) — summary пустая при ошибке.
    """
    if protect_last_n is None:
        protect_last_n = _protect_last_n()

    # Отделяем защищённые сообщения
    protected: list[dict] = []
    compact_input = list(messages)
    if protect_last_n > 0 and len(compact_input) > protect_last_n:
        protected = compact_input[-protect_last_n:] if isinstance(compact_input[-1], dict) else []
        compact_input = compact_input[:-protect_last_n]
    elif protect_last_n > 0:
        # Слишком мало сообщений — ничего не сжимаем
        return "", (list(messages) if isinstance(messages[-1], dict) else [])

    # Если сжимать нечего — возвращаем как есть
    if not compact_input:
        return "", protected

    lines: List[str] = []
    for m in compact_input:
        if isinstance(m, dict):
            role = str(m.get("role", "user"))[:12]
            text = str(m.get("content") or m.get("text") or "")
        else:
            role = "user"
            text = str(m)
        lines.append(f"[{role}]: {text[:600]}")
    body = "\n".join(lines)
    # Если диалог слишком большой — обрежем первые сообщения
    if len(body) > 8000:
        head = body[:3000]
        tail = body[-3000:]
        body = f"{head}\n... [{len(body)} символов, показано начало и конец] ...\n{tail}"

    prompt = _DIALOGUE_COMPACT_PROMPT.format(body=body)
    if max_tokens <= 0:
        max_tokens = compactor_max_summary_tokens()

    try:
        from core.llm_tiered import llm_generate_tiered

        result = await llm_generate_tiered(
            llm,
            tag="llm_compact_dialogue",
            prompt=prompt,
            max_tokens=max_tokens,
        )
        content = result.get("content") or ""
        if isinstance(content, str) and content.strip():
            return content.strip(), protected
    except Exception as e:
        logger.warning("[compactor] LLM dialogue compact failed: %s", e)
    return "", protected


async def compact_document_llm(
    llm: Any,
    text: str,
    max_tokens: int = 0,
    tag: str = "llm_compact_document",
) -> str:
    """
    Асинхронная LLM-суммаризация документа.
    Возвращает пустую строку при ошибке.
    """
    body = text[:4000]  # безопасный лимит для промпта
    prompt = _DOCUMENT_COMPACT_PROMPT.format(body=body)
    if max_tokens <= 0:
        max_tokens = compactor_max_summary_tokens()

    try:
        from core.llm_tiered import llm_generate_tiered

        result = await llm_generate_tiered(
            llm,
            tag=tag,
            prompt=prompt,
            max_tokens=max_tokens,
        )
        content = result.get("content") or ""
        if isinstance(content, str) and content.strip():
            return content.strip()
    except Exception as e:
        logger.warning("[compactor] LLM document compact failed: %s", e)
    return ""


# ── Встраивание результатов в prompt_parts ──


def inject_dialogue_compact(
    parts: Dict[str, Any],
    summary: str,
    protected_messages: list[dict],
    meta: Dict[str, Any],
) -> None:
    """
    Заменить recent_dialogue на LLM-сводку старых сообщений + verbatim protected.

    Итог: [system: сводка диалога] + [protected messages verbatim].
    """
    compacted: list[dict] = []
    if summary:
        compacted.append({
            "role": "system",
            "content": f"[Сводка диалога]: {summary}",
        })
    compacted.extend(protected_messages)
    parts["dialogue_summary_compacted"] = summary
    parts["recent_dialogue"] = compacted
    meta["dialogue_llm_compacted"] = bool(summary)
    meta["compacted"] = bool(summary)
    meta["dialogue_summary_len"] = len(summary) if summary else 0
    meta["dialogue_protected_count"] = len(protected_messages)


def inject_document_compact(parts: Dict[str, Any], summary: str, meta: Dict[str, Any]) -> None:
    """Вставить LLM-сводку документа в prompt_parts."""
    parts["document_intake_block"] = summary
    meta["document_llm_compacted"] = True
    meta["compacted"] = True
    meta["document_summary_len"] = len(summary)
