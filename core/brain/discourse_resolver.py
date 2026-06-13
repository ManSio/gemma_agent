"""
Единая точка разрешения дискурса: эллипсис, наследование нити, IUR-lite, thread judge.

Запускается до intent/profile routing. Не расширяет keyword-списки —
использует DSV, registry-heuristics, структурные метрики, last_qa_pair и опционально LLM judge.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional, Tuple

from core.brain.env import env_flag
from core.monitoring import MONITOR

logger = logging.getLogger(__name__)

ACTION_STAY = "stay"
ACTION_BRANCH = "branch"
ACTION_CORRECT = "correct"

_MODE_SWITCH_RE = re.compile(
    r"(?i)\b(?:переключ|switch|смени|mode|режим)\b",
)

_INHERIT_BLOCK_PROFILES = frozenset({"short", "batch", "task_executor"})

_JUDGE_ON_STRUCTURAL = frozenset({"structural"})


def _discourse_enabled() -> bool:
    """Проверить, включён ли discourse resolver."""
    return env_flag("DISCOURSE_RESOLVER_ENABLED", default=True)


def _struct_max_chars() -> int:
    """Максимальная длина реплики для структурного continuation."""
    raw = (
        os.getenv("DISCOURSE_STRUCT_MAX_CHARS")
        or os.getenv("USER_FACING_SHORT_TURN_MAX_CHARS")
        or "56"
    )
    try:
        return max(8, int(str(raw).strip()))
    except ValueError:
        return 56


def _struct_max_words() -> int:
    """Максимум слов для структурного continuation."""
    try:
        return max(2, int((os.getenv("DISCOURSE_STRUCT_MAX_WORDS") or "8").strip()))
    except ValueError:
        return 8


def _min_last_assistant_chars() -> int:
    """Минимальная длина last_assistant для наследования нити."""
    try:
        return max(20, int((os.getenv("DISCOURSE_MIN_LAST_ASSISTANT_CHARS") or "60").strip()))
    except ValueError:
        return 60


def _rewrite_enabled() -> bool:
    """Включить IUR-lite переписывание эллиптической реплики."""
    return env_flag("DISCOURSE_REWRITE_ENABLED", default=True)


@dataclass
class DiscourseResolution:
    """Результат разрешения дискурса для одного хода."""

    action: str = ACTION_BRANCH
    raw_user_text: str = ""
    effective_user_text: str = ""
    inherit_intent: str = ""
    inherit_profile: str = ""
    rewrite_applied: bool = False
    continuation: bool = False
    reason: str = ""
    hint: str = ""
    topic_summary: str = ""
    last_user_q: str = ""
    last_assistant_a: str = ""
    judge_source: str = "structural"
    confidence: float = 0.0
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Сериализовать resolution для context/audit."""
        return asdict(self)

    def to_audit(self) -> Dict[str, Any]:
        """Компактный аудит для turns.jsonl / router_route_audit."""
        return {
            "action": self.action,
            "continuation": self.continuation,
            "reason": self.reason,
            "inherit_intent": self.inherit_intent or None,
            "inherit_profile": self.inherit_profile or None,
            "rewrite": self.rewrite_applied,
            "topic_summary": self.topic_summary or None,
            "judge_source": self.judge_source,
            "confidence": round(float(self.confidence or 0.0), 3),
        }


def _dialogue_state(context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Извлечь dialogue_state из context."""
    ctx = context if isinstance(context, dict) else {}
    ds = ctx.get("dialogue_state")
    return dict(ds) if isinstance(ds, dict) else {}


def _recent_dialogue(context: Optional[Dict[str, Any]]) -> list:
    """Извлечь recent_dialogue из context."""
    ctx = context if isinstance(context, dict) else {}
    rd = ctx.get("recent_dialogue") or ctx.get("recent_messages")
    return rd if isinstance(rd, list) else []


def _last_qa_from_context(context: Optional[Dict[str, Any]]) -> Tuple[str, str]:
    """Последняя пара user/assistant из контекста."""
    rd = _recent_dialogue(context)
    if not rd:
        return "", ""
    try:
        from core.dialogue_recheck_anchor import last_qa_pair, last_substantive_user_question

        prev_q = str(last_substantive_user_question(rd, skip_current=True) or "").strip()
        last_a = ""
        pair = last_qa_pair(rd)
        if pair:
            if not prev_q:
                prev_q = str(pair[0] or "").strip()
            last_a = str(pair[1] or "").strip()
        return prev_q[:280], last_a[:400]
    except Exception as e:
        logger.debug("discourse last_qa: %s", e)
        return "", ""


def _topic_summary_from_context(context: Optional[Dict[str, Any]], last_user_q: str) -> str:
    """Активная тема: topic_tracking или последний вопрос пользователя."""
    ctx = context if isinstance(context, dict) else {}
    tt = ctx.get("topic_tracking")
    if isinstance(tt, dict):
        cur = str(tt.get("current") or "").strip()
        if cur:
            return cur[:120]
    if last_user_q:
        return last_user_q.split("\n")[0].strip()[:120]
    return ""


def _looks_mode_switch(text: str) -> bool:
    """Явная смена режима/темы пользователем."""
    low = (text or "").strip().lower()
    return bool(low and _MODE_SWITCH_RE.search(low))


def _strong_profile_shift(user_text: str, context: Optional[Dict[str, Any]]) -> str:
    """Сильный сигнал смены профиля из registry (не keyword-списки)."""
    try:
        from core.brain.profile_registry import profile_from_text_heuristics

        prof = profile_from_text_heuristics(user_text, planner_context=context)
        return str(prof or "").strip().lower()
    except Exception as e:
        logger.debug("discourse strong_profile_shift: %s", e)
        return ""


def _is_substantive_new_question(user_text: str) -> bool:
    """Самостоятельный вопрос с явным explain-маркером (существующий regex)."""
    try:
        from core.brain.user_facing_contract import _SUBSTANTIVE_Q_RE

        return bool(_SUBSTANTIVE_Q_RE.search(user_text or ""))
    except Exception:
        return False


def _inherit_profile_from_state(ds: Dict[str, Any]) -> str:
    """Профиль для наследования из dialogue_state."""
    try:
        from core.brain.profile_registry import is_valid_profile

        raw = str(
            ds.get("last_brain_profile") or ds.get("brain_profile") or ""
        ).strip().lower()
        if raw and is_valid_profile(raw) and raw not in _INHERIT_BLOCK_PROFILES:
            return raw
    except Exception as e:
        logger.debug("discourse inherit_profile: %s", e)
    return ""


def _batch_continuation_active(context: Optional[Dict[str, Any]]) -> bool:
    """Batch-продолжение имеет приоритет над discourse inherit."""
    ctx = context if isinstance(context, dict) else {}
    if ctx.get("brain_force_batch_profile"):
        return True
    try:
        from core.batch_continuation import get_pending, is_continuation

        raw = str(ctx.get("raw_user_text") or ctx.get("user_text") or "").strip()
        persisted = {
            "batch_pending": ctx.get("batch_pending"),
            "dialogue_state": ctx.get("dialogue_state"),
        }
        if raw and is_continuation(raw) and get_pending(persisted):
            return True
    except Exception as e:
        logger.debug("discourse batch guard: %s", e)
    return False


def _strip_ephemeral_dialogue_keys(ds: Dict[str, Any]) -> Dict[str, Any]:
    """Убрать одноходовые ключи discourse из dialogue_state перед persist."""
    out = dict(ds)
    for key in ("_discourse_inherit_intent", "_discourse_inherit_profile"):
        out.pop(key, None)
    return out


def _inherit_intent_from_state(ds: Dict[str, Any]) -> str:
    """Intent для наследования из dialogue_state."""
    raw = str(ds.get("last_intent") or "").strip().lower()
    if raw in {"", "empty", "unknown"}:
        return ""
    return raw


def _correction_signal(context: Optional[Dict[str, Any]], user_text: str) -> Tuple[bool, str]:
    """Сигнал коррекции пользователя (tone/loop), без inherit."""
    try:
        from core.brain.dialogue_context import build_dsv

        ctx = dict(context) if isinstance(context, dict) else {}
        ctx.setdefault("user_text", (user_text or "").strip())
        dsv = build_dsv(ctx)
        if dsv.correction_loop:
            return True, "correction_loop"
        if dsv.user_tone in {"angry", "testing"}:
            return True, f"tone:{dsv.user_tone}"
    except Exception as e:
        logger.debug("discourse correction_signal: %s", e)
    return False, ""


def structural_thread_continuation(
    user_text: str,
    context: Optional[Dict[str, Any]],
) -> Tuple[bool, str]:
    """
    Структурное продолжение нити без новых keyword-списков.

    Returns (should_inherit, reason).
    """
    if not _discourse_enabled():
        return False, "disabled"

    if _batch_continuation_active(context):
        return False, "batch_continuation"

    ut = (user_text or "").strip()
    if not ut:
        return False, "empty"

    corr, corr_reason = _correction_signal(context, ut)
    if corr:
        return False, corr_reason

    if _looks_mode_switch(ut):
        return False, "mode_switch"

    if _is_substantive_new_question(ut):
        return False, "substantive_question"

    strong = _strong_profile_shift(ut, context)
    if strong:
        return False, f"profile_shift:{strong}"

    if len(ut) > _struct_max_chars() or len(ut.split()) > _struct_max_words():
        return False, "too_long"

    try:
        from core.brain.dialogue_context import build_dsv

        ctx = dict(context) if isinstance(context, dict) else {}
        ctx.setdefault("user_text", ut)
        dsv = build_dsv(ctx)
    except Exception as e:
        logger.debug("discourse build_dsv: %s", e)
        return False, "dsv_error"

    if dsv.topic_change:
        return False, "topic_change"

    ds = _dialogue_state(context)
    last_intent = _inherit_intent_from_state(ds)
    last_profile = _inherit_profile_from_state(ds)
    if not last_intent and not last_profile:
        return False, "no_prior_state"

    last_a = str(dsv.last_assistant_excerpt or "").strip()
    if len(last_a) < _min_last_assistant_chars():
        return False, "short_last_assistant"

    try:
        from core.prompt_routing import infer_assistant_expects_reply

        if not infer_assistant_expects_reply(
            last_a,
            last_intent=last_intent or "general",
        ):
            return False, "no_expects_reply"
    except Exception as e:
        logger.debug("discourse expects_reply: %s", e)
        return False, "expects_reply_error"

    try:
        from core.brain.user_facing_contract import (
            classify_short_user_turn,
            is_short_turn_continuing_dialogue,
        )

        kind = classify_short_user_turn(
            ut,
            _recent_dialogue(context),
            last_assistant=last_a,
        )
        if is_short_turn_continuing_dialogue(kind):
            return True, f"short_kind:{kind}"
    except Exception as e:
        logger.debug("discourse short_kind: %s", e)

    return True, "structural"


def _rewrite_elliptical(
    user_text: str,
    context: Optional[Dict[str, Any]],
    *,
    prev_q: str = "",
) -> Tuple[str, bool]:
    """IUR-lite: привязать эллипсис к последнему содержательному вопросу."""
    if not _rewrite_enabled():
        return user_text, False

    ut = (user_text or "").strip()
    if not prev_q:
        prev_q, _ = _last_qa_from_context(context)
    if not prev_q or prev_q.lower() == ut.lower():
        return ut, False

    rewritten = f"{ut} (уточнение к предыдущему вопросу: «{prev_q[:280]}»)"
    return rewritten, True


def build_active_thread_block(res: DiscourseResolution) -> str:
    """Блок ACTIVE_THREAD для промпта brain."""
    if not res.continuation and res.action != ACTION_CORRECT:
        return ""
    parts = ["[ACTIVE_THREAD]"]
    if res.topic_summary:
        parts.append(f"Тема: {res.topic_summary[:120]}.")
    if res.last_user_q:
        parts.append(f"Последний вопрос пользователя: «{res.last_user_q[:200]}».")
    if res.last_assistant_a:
        parts.append(f"Последний ответ ассистента (суть): «{res.last_assistant_a[:200]}».")
    if res.raw_user_text:
        parts.append(f"Текущая реплика: «{res.raw_user_text[:200]}».")
    if res.action == ACTION_CORRECT:
        parts.append(
            "Пользователь поправляет или отвергает прошлый ответ — отвечай на исправленную нить, "
            "не смешивай с другими темами из истории."
        )
    else:
        parts.append(
            "Это продолжение текущей нити — не переключайся на другие темы из архива диалога."
        )
    return " ".join(parts)


def _build_hint(res: DiscourseResolution) -> str:
    """Подсказка для prompt assembly при continuation."""
    block = build_active_thread_block(res)
    return block.replace("[ACTIVE_THREAD] ", "").strip() if block else ""


def _apply_stay_resolution(
    res: DiscourseResolution,
    context: Optional[Dict[str, Any]],
    *,
    reason: str,
    judge_source: str = "structural",
    confidence: float = 0.0,
    resolved_override: str = "",
) -> DiscourseResolution:
    """Заполнить поля STAY-resolution."""
    ds = _dialogue_state(context)
    last_q, last_a = _last_qa_from_context(context)
    res.action = ACTION_STAY
    res.continuation = True
    res.reason = reason
    res.inherit_intent = _inherit_intent_from_state(ds)
    res.inherit_profile = _inherit_profile_from_state(ds)
    res.last_user_q = last_q
    res.last_assistant_a = last_a
    res.topic_summary = _topic_summary_from_context(context, last_q)
    res.judge_source = judge_source
    res.confidence = confidence

    if resolved_override:
        res.effective_user_text = resolved_override[:500]
        res.rewrite_applied = resolved_override.strip().lower() != res.raw_user_text.strip().lower()
    else:
        effective, rewritten = _rewrite_elliptical(res.raw_user_text, context, prev_q=last_q)
        res.effective_user_text = effective
        res.rewrite_applied = rewritten

    res.hint = _build_hint(res)
    MONITOR.inc("discourse_stay_total")
    if res.rewrite_applied:
        MONITOR.inc("discourse_rewrite_total")
    logger.info(
        "[discourse] stay intent=%s profile=%s rewrite=%s reason=%s src=%s",
        res.inherit_intent or "-",
        res.inherit_profile or "-",
        res.rewrite_applied,
        reason,
        judge_source,
    )
    return res


def resolve_discourse(
    user_text: str,
    context: Optional[Dict[str, Any]] = None,
) -> DiscourseResolution:
    """Разрешить дискурс синхронно (structural + IUR, без LLM judge)."""
    raw = (user_text or "").strip()
    res = DiscourseResolution(
        action=ACTION_BRANCH,
        raw_user_text=raw,
        effective_user_text=raw,
    )
    if not raw:
        res.reason = "empty"
        return res

    corr, corr_reason = _correction_signal(context, raw)
    if corr:
        res.action = ACTION_CORRECT
        res.reason = corr_reason
        res.last_user_q, res.last_assistant_a = _last_qa_from_context(context)
        res.topic_summary = _topic_summary_from_context(context, res.last_user_q)
        res.hint = _build_hint(res)
        MONITOR.inc("discourse_correct_total")
        return res

    inherit, reason = structural_thread_continuation(raw, context)
    res.reason = reason
    if not inherit:
        MONITOR.inc("discourse_branch_total")
        return res

    return _apply_stay_resolution(res, context, reason=reason, judge_source="structural", confidence=0.7)


async def resolve_discourse_async(
    user_text: str,
    context: Optional[Dict[str, Any]] = None,
    *,
    llm: Any = None,
) -> DiscourseResolution:
    """Разрешить дискурс с опциональным LLM thread judge на пограничных ходах."""
    res = resolve_discourse(user_text, context)
    if res.action in {ACTION_CORRECT, ACTION_BRANCH}:
        return res
    if res.reason not in _JUDGE_ON_STRUCTURAL:
        return res
    if llm is None:
        return res

    try:
        from core.brain.discourse_thread_judge import (
            ACTION_BRANCH as J_BRANCH,
            ACTION_CORRECT as J_CORRECT,
            ACTION_STAY as J_STAY,
            judge_thread_async,
        )

        judged = await judge_thread_async(llm, res.raw_user_text, context)
        if not judged:
            return res

        action = str(judged.get("thread_action") or "").strip().lower()
        conf = float(judged.get("confidence") or 0.0)
        if action == J_BRANCH:
            res.continuation = False
            res.action = ACTION_BRANCH
            res.reason = f"judge_branch:{conf:.2f}"
            res.judge_source = "llm"
            res.confidence = conf
            MONITOR.inc("discourse_branch_total")
            return res
        if action == J_CORRECT:
            res.action = ACTION_CORRECT
            res.continuation = False
            res.reason = f"judge_correct:{conf:.2f}"
            res.judge_source = "llm"
            res.confidence = conf
            res.last_user_q, res.last_assistant_a = _last_qa_from_context(context)
            res.topic_summary = str(judged.get("topic_summary") or "")[:120] or _topic_summary_from_context(
                context, res.last_user_q
            )
            resolved = str(judged.get("resolved_user_text") or "").strip()
            if resolved:
                res.effective_user_text = resolved[:500]
            res.hint = _build_hint(res)
            MONITOR.inc("discourse_correct_total")
            return res
        if action == J_STAY:
            topic = str(judged.get("topic_summary") or "").strip()
            resolved = str(judged.get("resolved_user_text") or "").strip()
            res = _apply_stay_resolution(
                res,
                context,
                reason=f"judge_stay:{conf:.2f}",
                judge_source="llm",
                confidence=conf,
                resolved_override=resolved,
            )
            if topic:
                res.topic_summary = topic[:120]
            return res
    except Exception as e:
        logger.debug("resolve_discourse_async judge: %s", e)

    return res


def _merge_discourse_into_context(
    ctx: Dict[str, Any],
    res: DiscourseResolution,
) -> Dict[str, Any]:
    """Записать resolution в context."""
    ctx["_discourse_applied"] = True
    ctx["discourse_resolution"] = res.to_dict()
    ctx["discourse_audit"] = res.to_audit()
    ctx["raw_user_text"] = res.raw_user_text
    ctx["user_text"] = res.effective_user_text

    active = build_active_thread_block(res)
    if active:
        ctx["active_thread_block"] = active

    if res.hint:
        prev = str(ctx.get("routing_prefs_hint") or "").strip()
        ctx["routing_prefs_hint"] = f"{prev}\n\n{res.hint}".strip() if prev else res.hint

    if res.continuation:
        ds = _strip_ephemeral_dialogue_keys(_dialogue_state(ctx))
        if res.inherit_intent:
            ds["_discourse_inherit_intent"] = res.inherit_intent
        if res.inherit_profile:
            ds["_discourse_inherit_profile"] = res.inherit_profile
        if res.topic_summary:
            ds["active_topic_summary"] = res.topic_summary[:120]
        ctx["dialogue_state"] = ds

    return ctx


def _needs_judge_upgrade(ctx: Dict[str, Any]) -> bool:
    """Нужно ли дожать structural resolution через LLM thread judge."""
    try:
        from core.brain.discourse_thread_judge import thread_judge_enabled

        if not thread_judge_enabled():
            return False
    except Exception as e:
        logger.debug("discourse judge upgrade check: %s", e)
        return False
    dr = ctx.get("discourse_resolution")
    if not isinstance(dr, dict):
        return False
    if not dr.get("continuation"):
        return False
    reason = str(dr.get("reason") or "")
    js = str(dr.get("judge_source") or "")
    return js == "structural" and reason in _JUDGE_ON_STRUCTURAL


def _ctx_for_judge_upgrade(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Контекст без ephemeral discourse-полей для повторного resolve."""
    skip = frozenset(
        {
            "_discourse_applied",
            "discourse_resolution",
            "discourse_audit",
            "active_thread_block",
            "raw_user_text",
        }
    )
    return {k: v for k, v in ctx.items() if k not in skip}


def _publish_discourse_context(
    owner: Optional[Dict[str, Any]],
    updated: Dict[str, Any],
) -> Dict[str, Any]:
    """Записать результат discourse в исходный dict (callers сохраняют ссылку на context)."""
    if isinstance(owner, dict):
        owner.clear()
        owner.update(updated)
        return owner
    return dict(updated)


def apply_discourse_to_context(
    user_text: str,
    context: Optional[Dict[str, Any]],
) -> Tuple[str, Dict[str, Any]]:
    """Применить discourse resolution к context (идемпотентно, без LLM)."""
    owner = context if isinstance(context, dict) else {}
    ctx = dict(owner)
    if ctx.get("_discourse_applied"):
        eff = str(ctx.get("user_text") or user_text or "").strip()
        return eff or (user_text or "").strip(), _publish_discourse_context(owner, ctx)

    res = resolve_discourse(user_text, ctx)
    ctx = _merge_discourse_into_context(ctx, res)
    return res.effective_user_text, _publish_discourse_context(owner, ctx)


async def apply_discourse_to_context_async(
    user_text: str,
    context: Optional[Dict[str, Any]],
    *,
    llm: Any = None,
) -> Tuple[str, Dict[str, Any]]:
    """Применить discourse resolution с LLM thread judge (идемпотентно)."""
    owner = context if isinstance(context, dict) else {}
    ctx = dict(owner)
    if ctx.get("_discourse_applied"):
        if _needs_judge_upgrade(ctx):
            raw = str(ctx.get("raw_user_text") or user_text or "").strip()
            base = _ctx_for_judge_upgrade(ctx)
            res = await resolve_discourse_async(raw or user_text, base, llm=llm)
            merged = _merge_discourse_into_context(base, res)
            return res.effective_user_text, _publish_discourse_context(owner, merged)
        eff = str(ctx.get("user_text") or user_text or "").strip()
        return eff or (user_text or "").strip(), _publish_discourse_context(owner, ctx)

    res = await resolve_discourse_async(user_text, ctx, llm=llm)
    ctx = _merge_discourse_into_context(ctx, res)
    return res.effective_user_text, _publish_discourse_context(owner, ctx)


def strip_ephemeral_discourse_state(dialogue_state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Публичный API: очистить ephemeral discourse-ключи перед записью на диск."""
    if not isinstance(dialogue_state, dict):
        return {}
    return _strip_ephemeral_dialogue_keys(dialogue_state)


def is_continuation_from_context(
    user_text: str,
    context: Optional[Dict[str, Any]] = None,
) -> bool:
    """Публичный API: реплика продолжает нить (для profile_registry)."""
    dr = (context or {}).get("discourse_resolution") if isinstance(context, dict) else None
    if isinstance(dr, dict) and dr.get("continuation"):
        return True
    inherit, _ = structural_thread_continuation(user_text, context)
    return inherit


def inherited_profile_from_context(context: Optional[Dict[str, Any]]) -> str:
    """Профиль из discourse resolution, если есть."""
    ctx = context if isinstance(context, dict) else {}
    dr = ctx.get("discourse_resolution")
    if isinstance(dr, dict):
        prof = str(dr.get("inherit_profile") or "").strip().lower()
        if prof:
            return prof
    ds = _dialogue_state(ctx)
    return str(ds.get("_discourse_inherit_profile") or "").strip().lower()


def inherited_intent_from_context(context: Optional[Dict[str, Any]]) -> str:
    """Intent из discourse resolution, если есть."""
    ctx = context if isinstance(context, dict) else {}
    dr = ctx.get("discourse_resolution")
    if isinstance(dr, dict):
        intent = str(dr.get("inherit_intent") or "").strip().lower()
        if intent:
            return intent
    ds = _dialogue_state(ctx)
    return str(ds.get("_discourse_inherit_intent") or "").strip().lower()
