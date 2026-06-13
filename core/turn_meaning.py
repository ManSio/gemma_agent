"""TurnMeaning — единый verdict намерения хода до routing и collapse."""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

from core.brain.env import env_flag
from core.monitoring import MONITOR

logger = logging.getLogger(__name__)

SPEECH_QUESTION = "question"
SPEECH_CORRECTION = "correction"
SPEECH_CONTINUATION = "continuation"
SPEECH_STATEMENT = "statement"

REFERENT_AGENT = "agent"
REFERENT_USER = "user"
REFERENT_WORLD = "world"
REFERENT_THREAD = "thread"

ACTION_STAY = "stay"
ACTION_BRANCH = "branch"
ACTION_CORRECT = "correct"


@dataclass
class TurnMeaning:
    """Вердикт смысла хода: один источник для discourse и audit."""

    speech_act: str = SPEECH_QUESTION
    referent: str = REFERENT_WORLD
    thread_action: str = ACTION_BRANCH
    inherit_thread: bool = False
    source: str = "structural"
    confidence: float = 0.0
    reason: str = ""
    resolved_user_text: str = ""
    topic_summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация для context и turns.jsonl."""
        return asdict(self)

    def to_audit(self) -> Dict[str, Any]:
        """Компактный аудит для turn_observer."""
        return {
            "speech_act": self.speech_act or None,
            "referent": self.referent or None,
            "thread_action": self.thread_action or None,
            "inherit_thread": self.inherit_thread,
            "meaning_source": self.source or None,
            "meaning_confidence": round(float(self.confidence or 0.0), 3),
            "meaning_reason": (self.reason or None)[:80] if self.reason else None,
        }


def turn_meaning_enabled() -> bool:
    """Включён ли слой TurnMeaning (fallback: structural только)."""
    return env_flag("TURN_MEANING_ENABLED", default=True)


def _recent_dialogue_nonempty(context: Optional[Dict[str, Any]]) -> bool:
    """Есть ли непустая недавняя переписка в context."""
    ctx = context if isinstance(context, dict) else {}
    rd = ctx.get("recent_dialogue") or ctx.get("recent_messages")
    return isinstance(rd, list) and len(rd) >= 2


def _structural_referent_from_text(user_text: str) -> tuple[str, str]:
    """Structural referent user/agent из существующих identity-маркеров."""
    raw = (user_text or "").strip()
    if not raw:
        return "", ""
    try:
        from core.user_facts import plain_text_requests_user_facts_identity

        if plain_text_requests_user_facts_identity(raw):
            return REFERENT_USER, "user_facts_identity"
    except Exception as e:
        logger.debug("structural referent user: %s", e)
    try:
        from core.user_facts import _BOT_NAME_QUESTION_RE

        if _BOT_NAME_QUESTION_RE.search(raw):
            return REFERENT_AGENT, "bot_name_question"
    except Exception as e:
        logger.debug("structural referent agent name: %s", e)
    low = raw.lower()
    if "?" in raw or low.startswith(
        ("какие ", "что ", "где ", "когда ", "почему ", "зачем ", "кто ", "как ")
    ):
        agent_markers = (
            " у тебя",
            " тебе ",
            " тебя ",
            " ты ",
            "ты ",
            " your ",
            " you ",
        )
        if any(m in f" {low} " for m in agent_markers):
            return REFERENT_AGENT, "second_person_question"
    return "", ""


def resolve_turn_meaning_structural(
    user_text: str,
    context: Optional[Dict[str, Any]] = None,
) -> TurnMeaning:
    """Structural verdict из метаданных и существующих discourse-сигналов."""
    raw = (user_text or "").strip()
    ctx = context if isinstance(context, dict) else {}

    try:
        from core.brain.discourse_resolver import (
            _correction_signal,
            _is_substantive_new_question,
            _prior_assistant_turn_unsatisfactory,
            structural_thread_continuation,
        )
        from core.brain.user_facing_contract import classify_short_user_turn

        corr, corr_reason = _correction_signal(ctx, raw)
        if corr:
            MONITOR.inc("turn_meaning_structural_total")
            MONITOR.inc("turn_meaning_correct_total")
            return TurnMeaning(
                speech_act=SPEECH_CORRECTION,
                referent=REFERENT_THREAD,
                thread_action=ACTION_CORRECT,
                inherit_thread=False,
                source="structural",
                confidence=0.85,
                reason=corr_reason or "correction_signal",
            )

        if _prior_assistant_turn_unsatisfactory(ctx):
            last_a = ""
            try:
                from core.brain.dialogue_context import build_dsv

                dsv = build_dsv({**ctx, "user_text": raw})
                last_a = str(dsv.last_assistant_excerpt or "")
            except Exception as e:
                logger.debug("turn_meaning dsv: %s", e)
            kind = classify_short_user_turn(
                raw,
                ctx.get("recent_dialogue") or ctx.get("recent_messages"),
                last_assistant=last_a,
            )
            if kind == "normal":
                MONITOR.inc("turn_meaning_structural_total")
                MONITOR.inc("turn_meaning_correct_total")
                return TurnMeaning(
                    speech_act=SPEECH_CORRECTION,
                    referent=REFERENT_THREAD,
                    thread_action=ACTION_CORRECT,
                    inherit_thread=False,
                    source="metadata",
                    confidence=0.9,
                    reason="prior_unsatisfactory",
                )

        referent, ref_reason = _structural_referent_from_text(raw)
        if referent:
            speech = SPEECH_QUESTION if "?" in raw else SPEECH_STATEMENT
            MONITOR.inc("turn_meaning_structural_total")
            MONITOR.inc("turn_meaning_branch_total")
            MONITOR.inc(f"turn_meaning_referent_{referent}_total")
            return TurnMeaning(
                speech_act=speech,
                referent=referent,
                thread_action=ACTION_BRANCH,
                inherit_thread=False,
                source="structural",
                confidence=0.82,
                reason=ref_reason,
            )

        if _is_substantive_new_question(raw):
            MONITOR.inc("turn_meaning_structural_total")
            MONITOR.inc("turn_meaning_branch_total")
            return TurnMeaning(
                speech_act=SPEECH_QUESTION,
                referent=REFERENT_WORLD,
                thread_action=ACTION_BRANCH,
                inherit_thread=False,
                source="structural",
                confidence=0.8,
                reason="substantive_question",
            )

        inherit, reason = structural_thread_continuation(raw, ctx)
        if inherit:
            MONITOR.inc("turn_meaning_structural_total")
            MONITOR.inc("turn_meaning_stay_total")
            return TurnMeaning(
                speech_act=SPEECH_CONTINUATION,
                referent=REFERENT_THREAD,
                thread_action=ACTION_STAY,
                inherit_thread=True,
                source="structural",
                confidence=0.7,
                reason=reason or "structural",
            )

        speech = SPEECH_QUESTION if "?" in raw else SPEECH_STATEMENT
        MONITOR.inc("turn_meaning_structural_total")
        MONITOR.inc("turn_meaning_branch_total")
        return TurnMeaning(
            speech_act=speech,
            referent=REFERENT_WORLD,
            thread_action=ACTION_BRANCH,
            inherit_thread=False,
            source="structural",
            confidence=0.65,
            reason=reason or "default_branch",
        )
    except Exception as e:
        logger.debug("resolve_turn_meaning_structural: %s", e)
        return TurnMeaning(
            speech_act=SPEECH_QUESTION,
            referent=REFERENT_WORLD,
            thread_action=ACTION_BRANCH,
            inherit_thread=False,
            source="structural",
            confidence=0.5,
            reason="fallback_error",
        )


def turn_meaning_llm_needed(meaning: TurnMeaning, context: Optional[Dict[str, Any]] = None) -> bool:
    """Нужен ли LLM judge для disambiguation referent / continuation."""
    if not turn_meaning_enabled():
        return False
    try:
        from core.brain.discourse_thread_judge import thread_judge_enabled

        if not thread_judge_enabled():
            return False
    except Exception as e:
        logger.debug("turn_meaning_llm_needed judge: %s", e)
        return False
    if meaning.thread_action == ACTION_CORRECT:
        return False
    if meaning.thread_action == ACTION_STAY and meaning.inherit_thread:
        return True
    if meaning.thread_action == ACTION_BRANCH and meaning.speech_act == SPEECH_QUESTION:
        return _recent_dialogue_nonempty(context)
    return False


def _merge_judge_into_meaning(
    structural: TurnMeaning,
    judged: Dict[str, Any],
) -> TurnMeaning:
    """Применить LLM verdict поверх structural baseline."""
    action = str(judged.get("thread_action") or structural.thread_action).strip().lower()
    if action not in {ACTION_STAY, ACTION_BRANCH, ACTION_CORRECT}:
        action = structural.thread_action
    speech = str(judged.get("speech_act") or structural.speech_act).strip().lower()
    if speech not in {SPEECH_QUESTION, SPEECH_CORRECTION, SPEECH_CONTINUATION, SPEECH_STATEMENT}:
        speech = structural.speech_act
    referent = str(judged.get("referent") or structural.referent).strip().lower()
    if referent not in {REFERENT_AGENT, REFERENT_USER, REFERENT_WORLD, REFERENT_THREAD}:
        referent = structural.referent
    inherit = judged.get("inherit_thread")
    if inherit is None:
        inherit = action == ACTION_STAY and action != ACTION_CORRECT
    else:
        inherit = bool(inherit)
    if action == ACTION_CORRECT:
        inherit = False
        speech = SPEECH_CORRECTION
    conf = float(judged.get("confidence") or structural.confidence or 0.0)
    resolved = str(judged.get("resolved_user_text") or "").strip()
    topic = str(judged.get("topic_summary") or "").strip()
    reason = f"llm:{action}"
    if judged.get("source") == "llm":
        MONITOR.inc("turn_meaning_llm_ok_total")
    return TurnMeaning(
        speech_act=speech,
        referent=referent,
        thread_action=action,
        inherit_thread=inherit,
        source="llm",
        confidence=conf,
        reason=reason,
        resolved_user_text=resolved[:500],
        topic_summary=topic[:120],
    )


async def resolve_turn_meaning_async(
    user_text: str,
    context: Optional[Dict[str, Any]] = None,
    *,
    llm: Any = None,
) -> TurnMeaning:
    """Structural verdict + опциональный LLM judge на пограничных ходах."""
    structural = resolve_turn_meaning_structural(user_text, context)
    if not turn_meaning_llm_needed(structural, context) or llm is None:
        return structural
    try:
        from core.brain.discourse_thread_judge import judge_thread_async

        judged = await judge_thread_async(llm, user_text, context)
        if judged:
            return _merge_judge_into_meaning(structural, judged)
    except Exception as e:
        logger.debug("resolve_turn_meaning_async: %s", e)
        MONITOR.inc("turn_meaning_llm_fail_total")
    return structural


def apply_turn_meaning_to_context(
    context: Dict[str, Any],
    meaning: TurnMeaning,
) -> Dict[str, Any]:
    """Записать TurnMeaning в context для discourse и audit."""
    ctx = dict(context)
    ctx["turn_meaning"] = meaning.to_dict()
    ctx["turn_meaning_audit"] = meaning.to_audit()
    return ctx


def routing_hint_for_meaning(meaning: TurnMeaning | Dict[str, Any]) -> str:
    """Подсказка маршрутизатору/brain по referent (без keyword-списков)."""
    if isinstance(meaning, dict):
        referent = str(meaning.get("referent") or "").strip().lower()
        thread_action = str(meaning.get("thread_action") or "").strip().lower()
    else:
        referent = meaning.referent
        thread_action = meaning.thread_action
    if referent == REFERENT_AGENT and thread_action != ACTION_CORRECT:
        return (
            "Пользователь спрашивает о тебе как об ассистенте в этом диалоге "
            "(твои ограничения, ошибки, поведение сейчас) — не уходи в абстрактную философию ИИ."
        )
    if thread_action == ACTION_CORRECT:
        return (
            "Пользователь исправляет предыдущий ответ. Вернись к его исходному вопросу, "
            "не перечисляй факты профиля и не меняй тему."
        )
    return ""
