"""TurnStateVector — коллапс гипотез одного хода в наблюдаемое состояние."""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_BAD_PRIOR_OUTCOMES = frozenset({"clarify", "error", "fail", "partial", "empty"})


@dataclass
class TurnStateVector:
    """Единое состояние хода после discourse + slot reconcile."""

    raw_user_text: str = ""
    effective_user_text: str = ""
    discourse_action: str = ""
    discourse_reason: str = ""
    discourse_continuation: bool = False
    prior_outcome: str = ""
    expects_correction: bool = False
    slot_kind_before: str = ""
    slot_kind_after: str = ""
    slot_cleared: bool = False
    active_slot_kind: str = ""
    thread_topic: str = ""
    short_turn_kind: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация для context и turns.jsonl."""
        return asdict(self)

    def to_audit(self) -> Dict[str, Any]:
        """Компактный аудит для turn_observer."""
        return {
            "discourse_action": self.discourse_action or None,
            "discourse_reason": self.discourse_reason or None,
            "prior_outcome": self.prior_outcome or None,
            "expects_correction": self.expects_correction,
            "slot_cleared": self.slot_cleared,
            "active_slot_kind": self.active_slot_kind or None,
            "short_turn_kind": self.short_turn_kind or None,
        }


def _session_outcome(context: Dict[str, Any], persisted: Optional[Dict[str, Any]]) -> str:
    """last_outcome из context или persisted."""
    st = context.get("session_task")
    if isinstance(st, dict) and st.get("last_outcome"):
        return str(st.get("last_outcome") or "").strip().lower()
    if isinstance(persisted, dict):
        pst = persisted.get("session_task")
        if isinstance(pst, dict):
            return str(pst.get("last_outcome") or "").strip().lower()
    return ""


def build_turn_state(
    user_text: str,
    context: Optional[Dict[str, Any]],
    *,
    persisted: Optional[Dict[str, Any]] = None,
    slot_before: str = "",
    slot_after: str = "",
) -> TurnStateVector:
    """Собрать TSV после discourse и reconcile слотов."""
    ctx = context if isinstance(context, dict) else {}
    raw = str(ctx.get("raw_user_text") or user_text or "").strip()
    effective = str(ctx.get("user_text") or user_text or raw).strip()
    dr = ctx.get("discourse_resolution")
    dr = dr if isinstance(dr, dict) else {}

    prior = _session_outcome(ctx, persisted)
    short_kind = "normal"
    last_a = ""
    try:
        from core.brain.user_facing_contract import classify_short_user_turn

        rd = ctx.get("recent_dialogue") or ctx.get("recent_messages")
        ds = ctx.get("dialogue_state")
        if isinstance(ds, dict):
            last_a = str(ds.get("last_assistant_excerpt") or "").strip()
        short_kind = classify_short_user_turn(raw, rd, last_assistant=last_a)
    except Exception as e:
        logger.debug("turn_state short_kind: %s", e)

    expects_correction = bool(
        prior in _BAD_PRIOR_OUTCOMES and short_kind == "normal" and len(raw) <= 56
    )
    topic = str(dr.get("topic_summary") or "").strip()[:120]
    if not topic:
        tt = ctx.get("topic_tracking")
        if isinstance(tt, dict):
            topic = str(tt.get("current") or "").strip()[:120]

    cleared = bool(slot_before and not slot_after)
    return TurnStateVector(
        raw_user_text=raw,
        effective_user_text=effective,
        discourse_action=str(dr.get("action") or ctx.get("discourse_action") or "").strip(),
        discourse_reason=str(dr.get("reason") or "").strip(),
        discourse_continuation=bool(dr.get("continuation")),
        prior_outcome=prior,
        expects_correction=expects_correction,
        slot_kind_before=slot_before,
        slot_kind_after=slot_after,
        slot_cleared=cleared,
        active_slot_kind=slot_after,
        thread_topic=topic,
        short_turn_kind=short_kind,
    )


def collapse_turn_state(
    user_text: str,
    context: Dict[str, Any],
    *,
    persisted: Optional[Dict[str, Any]] = None,
) -> tuple[Dict[str, Any], TurnStateVector, bool]:
    """Сверить слоты, собрать TSV, записать в context; вернуть (ctx, tsv, mutated)."""
    from core.dialogue_slots import clear_slot, get_active_slot, resolve_slot_for_turn
    from core.slot_registry import slot_accepts_turn

    ctx = dict(context)
    rec = persisted if isinstance(persisted, dict) else None
    mutated = False
    slot_before = ""
    slot_after = ""

    if rec is not None:
        try:
            before = get_active_slot(rec)
            slot_before = str((before or {}).get("kind") or "").strip()
            rd = ctx.get("recent_dialogue") or ctx.get("recent_messages")
            resolve_slot_for_turn(user_text, rd, rec)
            active = get_active_slot(rec)
            if active:
                kind = str(active.get("kind") or "").strip()
                if kind and not slot_accepts_turn(kind, user_text, rd, persisted=rec):
                    clear_slot(rec)
                    active = None
                    mutated = True
            after = get_active_slot(rec)
            slot_after = str((after or {}).get("kind") or "").strip()
            if before != after:
                mutated = True
        except Exception as e:
            logger.debug("collapse_turn_state slots: %s", e)

    tsv = build_turn_state(
        user_text,
        ctx,
        persisted=rec,
        slot_before=slot_before,
        slot_after=slot_after,
    )
    ctx["turn_state"] = tsv.to_dict()
    ctx["turn_state_audit"] = tsv.to_audit()
    ctx["active_dialogue_slot_kind"] = tsv.active_slot_kind
    if tsv.discourse_action:
        ctx["discourse_action"] = tsv.discourse_action
    return ctx, tsv, mutated
