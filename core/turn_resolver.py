"""
DEPRECATED (2026-05-25): не подключать в orchestrator/plan.

Реформа brain-centric: `core/brain_own_turn.py` + pipeline; gate-слой не расширяем.
См. `docs/BRAIN_CENTRIC_REFORM_PLAN_RU.md` §8.

---
Единый арбитр хода: по умолчанию brain, shortcut только при явном разрешении.

Не добавляет стоп-слов. Использует heuristic_context_gate + dialogue_state + correction.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from core.context_perception import build_context_perception
from core.runtime_telegram_settings import effective_bool

logger = logging.getLogger(__name__)

_SHORTCUT_IDS = frozenset(
    {
        "weather_direct",
        "news_direct",
        "news_item_pick",
        "geo_nearby",
        "affirmative_search",
        "reminder_cancel",
        "reminder_schedule",
    }
)


def turn_resolver_enabled() -> bool:
    return effective_bool("TURN_RESOLVER_ENABLED", default=False)


@dataclass
class TurnVerdict:
    """Один ход — одно решение маршрутизации до LLM."""

    primary: str  # brain | weather_direct | news_direct | ...
    allowed_shortcuts: Set[str] = field(default_factory=set)
    blocked_shortcuts: Dict[str, str] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)
    perception: Dict[str, Any] = field(default_factory=dict)
    force_brain: bool = False

    def allows(self, shortcut_id: str) -> bool:
        if self.force_brain:
            return False
        if not turn_resolver_enabled():
            # Модуль не в orchestrator; не имитировать «разрешено всё».
            return False
        return shortcut_id in self.allowed_shortcuts

    def to_dict(self) -> Dict[str, Any]:
        return {
            "primary": self.primary,
            "allowed_shortcuts": sorted(self.allowed_shortcuts),
            "blocked_shortcuts": dict(self.blocked_shortcuts),
            "reasons": list(self.reasons),
            "force_brain": self.force_brain,
            "perception": dict(self.perception),
        }


def _pending_correction_active(persisted: Dict[str, Any]) -> bool:
    rp = persisted.get("routing_prefs")
    if not isinstance(rp, dict):
        return False
    pc = rp.get("pending_correction")
    return isinstance(pc, dict) and bool(pc.get("instruction") or pc.get("text"))


def _gate_allows(shortcut_id: str, user_text: str, persisted: Dict[str, Any]) -> bool:
    try:
        from core.heuristic_context_gate import should_run_shortcut

        recent = persisted.get("recent_messages")
        ds = persisted.get("dialogue_state")
        if not isinstance(ds, dict):
            ds = {}
        gr = should_run_shortcut(
            shortcut_id,
            user_text,
            persisted=persisted,
            planner_context={"recent_dialogue": recent} if recent else None,
        )
        return bool(gr.allowed)
    except Exception as e:
        logger.debug("turn_resolver gate %s: %s", shortcut_id, e)
        return False


def resolve_turn_verdict(
    user_text: str,
    *,
    persisted: Optional[Dict[str, Any]] = None,
    input_meta: Optional[Dict[str, Any]] = None,
) -> TurnVerdict:
    """
    Политика:
    - correction / paste / prose без explicit → brain
    - иначе не более одного shortcut; 2+ candidates → brain (ambiguous)
    """
    text = (user_text or "").strip()
    rec = dict(persisted) if isinstance(persisted, dict) else {}
    recent = rec.get("recent_messages")
    perception = build_context_perception(
        text, persisted=rec, recent_dialogue=recent, input_meta=input_meta
    )
    blocked: Dict[str, str] = {}
    reasons: List[str] = []
    candidates: Set[str] = set()

    if _pending_correction_active(rec):
        reasons.append("pending_correction_active")
        return TurnVerdict(
            primary="brain",
            allowed_shortcuts=set(),
            blocked_shortcuts={k: "correction_active" for k in _SHORTCUT_IDS},
            reasons=reasons,
            perception=perception,
            force_brain=True,
        )

    try:
        from core.dialogue_feedback_signals import user_feedback_likely

        if user_feedback_likely(text):
            reasons.append("user_feedback_reset")
            return TurnVerdict(
                primary="brain",
                allowed_shortcuts=set(),
                blocked_shortcuts={k: "user_feedback" for k in _SHORTCUT_IDS},
                reasons=reasons,
                perception=perception,
                force_brain=True,
            )
    except Exception as e:
        logger.debug("turn_resolver feedback: %s", e)

    if perception.get("pasted_article"):
        blocked["facts_auto_extract"] = "pasted_article"
        reasons.append("pasted_article_brain_only")
        for sid in _SHORTCUT_IDS:
            blocked.setdefault(sid, "pasted_article")
        return TurnVerdict(
            primary="brain",
            allowed_shortcuts=set(),
            blocked_shortcuts=blocked,
            reasons=reasons,
            perception=perception,
            force_brain=True,
        )

    try:
        from core.heuristic_context_gate import TurnDecisionContext, prose_score

        ctx = TurnDecisionContext(
            user_text=text,
            recent_dialogue=recent if isinstance(recent, list) else [],
            dialogue_state=rec.get("dialogue_state") if isinstance(rec.get("dialogue_state"), dict) else {},
            prose_score=prose_score(text),
            text_len=len(text),
        )
        try:
            from core.heuristic_context_gate import _prose_max_chars

            prose_max = _prose_max_chars()
        except Exception:
            prose_max = 140
        if ctx.text_len > prose_max and ctx.prose_score >= 0.35:
            explicit_any = any(
                _gate_allows(sid, text, rec)
                for sid in (
                    "weather_direct",
                    "news_direct",
                    "news_item_pick",
                    "geo_nearby",
                    "reminder_cancel",
                    "reminder_schedule",
                )
            )
            if not explicit_any:
                reasons.append("prose_without_explicit_command")
                return TurnVerdict(
                    primary="brain",
                    allowed_shortcuts=set(),
                    blocked_shortcuts={k: "prose" for k in _SHORTCUT_IDS},
                    reasons=reasons,
                    perception=perception,
                    force_brain=True,
                )
    except Exception as e:
        logger.debug("turn_resolver prose: %s", e)

    for sid in _SHORTCUT_IDS:
        if sid == "affirmative_search":
            try:
                from core.brain.text_helpers import (
                    assistant_offered_search_followup,
                    looks_like_affirmative_short,
                )

                if not (
                    looks_like_affirmative_short(text)
                    and assistant_offered_search_followup(recent)
                ):
                    blocked[sid] = "no_affirmative_offer"
                    continue
            except Exception:
                blocked[sid] = "affirmative_check_failed"
                continue
        if _gate_allows(sid, text, rec):
            candidates.add(sid)
        else:
            blocked[sid] = "gate_blocked"

    if len(candidates) > 1:
        reasons.append(f"ambiguous_shortcuts={sorted(candidates)}")
        return TurnVerdict(
            primary="brain",
            allowed_shortcuts=set(),
            blocked_shortcuts={**blocked, **{c: "ambiguous" for c in candidates}},
            reasons=reasons,
            perception=perception,
            force_brain=True,
        )

    if len(candidates) == 1:
        primary = next(iter(candidates))
        reasons.append(f"shortcut_allowed={primary}")
        return TurnVerdict(
            primary=primary,
            allowed_shortcuts=set(candidates),
            blocked_shortcuts=blocked,
            reasons=reasons,
            perception=perception,
            force_brain=False,
        )

    reasons.append("default_brain")
    return TurnVerdict(
        primary="brain",
        allowed_shortcuts=set(),
        blocked_shortcuts=blocked,
        reasons=reasons,
        perception=perception,
        force_brain=False,
    )


def plan_shortcut_enabled(
    verdict: Optional[TurnVerdict],
    shortcut_id: str,
) -> bool:
    """Для orchestrator.plan: можно ли вызывать try_*_reply."""
    if not turn_resolver_enabled():
        return False
    if verdict is None:
        return False
    return verdict.allows(shortcut_id)
