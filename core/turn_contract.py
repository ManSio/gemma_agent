"""TurnContract: generation token, lane, fingerprint — единый audit на ход."""
from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.monitoring import MONITOR

logger = logging.getLogger(__name__)

LANE_DIALOGUE = "DIALOGUE"
LANE_FACT = "FACT"
LANE_DEEP = "DEEP"

_FACT_PROFILES = frozenset(
    {
        "weather",
        "news",
        "math",
        "identity",
        "facts",
        "reminder",
        "schedule",
    }
)
_DEEP_PROFILES = frozenset(
    {
        "thorough",
        "research",
        "agent",
        "code",
        "analysis",
        "deep",
    }
)
_FACT_SHORTCUTS = frozenset(
    {
        "weather_direct",
        "weather_followup",
        "geo_nearby",
        "news_direct",
        "news_web_search",
        "news_item_direct",
        "referential_math",
        "user_facts_identity_nl",
        "session_meta_recall_nl",
        "dialog_recall_nl",
        "pre_llm",
    }
)


def turn_contract_enabled() -> bool:
    """Включён ли TurnContract (generation, fingerprint, audit)."""
    raw = os.getenv("TURN_CONTRACT_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def chat_key(user_id: str, group_id: Optional[str] = None) -> str:
    """Стабильный ключ чата для generation token."""
    uid = str(user_id or "").strip()
    gid = str(group_id or "").strip()
    return f"{uid}:{gid}" if gid else uid


def recent_dialogue_fingerprint(
    recent_dialogue: Any,
    *,
    tail: int = 6,
) -> str:
    """Компактный отпечаток STM для alert на залипание контекста."""
    if not isinstance(recent_dialogue, list) or not recent_dialogue:
        return ""
    rows: List[str] = []
    for row in recent_dialogue[-max(1, tail) :]:
        if not isinstance(row, dict):
            continue
        role = str(row.get("role") or "")[:12]
        text = str(row.get("text") or row.get("content") or "").strip()[:240]
        if text:
            rows.append(f"{role}:{text}")
    if not rows:
        return ""
    blob = "\n".join(rows)
    return hashlib.sha256(blob.encode("utf-8", errors="replace")).hexdigest()[:16]


def lane_from_profile(profile: str, *, short_circuit: str = "") -> str:
    """Сжать profile/shortcut в DIALOGUE | FACT | DEEP."""
    sc = (short_circuit or "").strip().lower()
    if sc in _FACT_SHORTCUTS or sc.startswith("weather") or sc.startswith("news"):
        return LANE_FACT
    prof = (profile or "").strip().lower()
    if not prof:
        return LANE_DIALOGUE
    for token in prof.replace("-", "_").split("_"):
        if token in _DEEP_PROFILES:
            return LANE_DEEP
        if token in _FACT_PROFILES:
            return LANE_FACT
    if prof in _DEEP_PROFILES:
        return LANE_DEEP
    if prof in _FACT_PROFILES:
        return LANE_FACT
    return LANE_DIALOGUE


def topic_anchor_from_context(
    *,
    turn_meaning: Any = None,
    persisted: Optional[Dict[str, Any]] = None,
    user_text: str = "",
) -> str:
    """Одна строка якоря темы для prompt/audit."""
    tm = turn_meaning
    if hasattr(tm, "to_dict"):
        tm = tm.to_dict()
    if isinstance(tm, dict):
        ref = str(tm.get("referent") or "").strip()
        act = str(tm.get("thread_action") or tm.get("action") or "").strip()
        if ref == "thread" and act:
            return f"thread:{act}"[:120]
        if ref:
            return f"referent:{ref}"[:120]
    if isinstance(persisted, dict):
        tt = persisted.get("topic_tracking")
        if isinstance(tt, dict):
            cur = str(tt.get("current") or "").strip()
            if cur:
                return cur[:120]
        ds = persisted.get("dialogue_state")
        if isinstance(ds, dict):
            slot = str(ds.get("dialogue_slot_kind") or ds.get("active_slot") or "").strip()
            if slot:
                return f"slot:{slot}"[:120]
    ut = (user_text or "").strip()
    if ut:
        return ut[:80]
    return ""


def must_blocks_from_context(
    turn_meaning: Any = None,
    context: Optional[Dict[str, Any]] = None,
) -> tuple:
    """Обязательные prompt blocks из correction/stay контекста."""
    try:
        from core.turn_correction_contract import must_blocks_for_context

        ctx = dict(context) if isinstance(context, dict) else {}
        if turn_meaning is not None:
            if hasattr(turn_meaning, "to_dict"):
                ctx.setdefault("turn_meaning", turn_meaning.to_dict())
            elif isinstance(turn_meaning, dict):
                ctx.setdefault("turn_meaning", turn_meaning)
        return must_blocks_for_context(ctx)
    except Exception as e:
        logger.debug("must_blocks_from_context: %s", e)
        return tuple()


@dataclass
class TurnContract:
    """Логически frozen контракт на один inbound→store цикл."""

    generation: int = 0
    trace_id: str = ""
    referent: str = ""
    lane: str = LANE_DIALOGUE
    active_slot: Optional[str] = None
    short_circuit: Optional[str] = None
    topic_anchor: str = ""
    recent_fingerprint: str = ""
    must_blocks: tuple = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация для input_meta и turns.jsonl."""
        out: Dict[str, Any] = {
            "generation": int(self.generation),
            "trace_id": str(self.trace_id or "")[:64],
            "referent": str(self.referent or "")[:16],
            "lane": str(self.lane or LANE_DIALOGUE)[:16],
            "topic_anchor": str(self.topic_anchor or "")[:120],
            "recent_fingerprint": str(self.recent_fingerprint or "")[:16],
        }
        if self.active_slot:
            out["active_slot"] = str(self.active_slot)[:32]
        if self.short_circuit:
            out["short_circuit"] = str(self.short_circuit)[:48]
        if self.must_blocks:
            out["must_blocks"] = list(self.must_blocks)[:8]
        return out

    def is_stale(self, current_generation: int) -> bool:
        """True если inbound generation устарел относительно чата."""
        if self.generation <= 0 or current_generation <= 0:
            return False
        return int(current_generation) != int(self.generation)


def build_turn_contract(
    *,
    trace_id: str = "",
    generation: int = 0,
    turn_meaning: Any = None,
    persisted: Optional[Dict[str, Any]] = None,
    user_text: str = "",
    short_circuit: str = "",
    profile: str = "",
    input_meta: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Any]] = None,
) -> TurnContract:
    """Собрать TurnContract из plan context и persisted STM."""
    tm = turn_meaning
    if tm is None and isinstance(input_meta, dict):
        tm = input_meta.get("plan_turn_meaning")
    if hasattr(tm, "to_dict"):
        tm_dict = tm.to_dict()
    elif isinstance(tm, dict):
        tm_dict = tm
    else:
        tm_dict = {}
    referent = str(tm_dict.get("referent") or "").strip()
    sc = (short_circuit or "").strip()
    if not sc and isinstance(input_meta, dict):
        sc = str(
            input_meta.get("pre_llm_lane")
            or input_meta.get("planner_bypass")
            or input_meta.get("fallback_variant")
            or ""
        ).strip()
    recent = None
    if isinstance(persisted, dict):
        recent = persisted.get("recent_messages")
    fp = recent_dialogue_fingerprint(recent)
    slot = None
    if isinstance(persisted, dict):
        ds = persisted.get("dialogue_state")
        if isinstance(ds, dict):
            slot = str(ds.get("dialogue_slot_kind") or ds.get("active_slot") or "").strip() or None
    gen = int(generation or 0)
    if gen <= 0 and isinstance(input_meta, dict):
        try:
            gen = int(input_meta.get("turn_generation") or 0)
        except (TypeError, ValueError):
            gen = 0
    contract = TurnContract(
        generation=gen,
        trace_id=str(trace_id or "")[:64],
        referent=referent,
        lane=lane_from_profile(profile, short_circuit=sc),
        active_slot=slot,
        short_circuit=sc or None,
        topic_anchor=topic_anchor_from_context(
            turn_meaning=tm_dict,
            persisted=persisted,
            user_text=user_text,
        ),
        recent_fingerprint=fp,
        must_blocks=must_blocks_from_context(tm_dict, context=context),
    )
    MONITOR.inc("turn_contract_built_total")
    return contract


def contract_audit_dict(contract: Any) -> Dict[str, Any]:
    """Компактный audit для turn.outcome / turns.jsonl."""
    if isinstance(contract, TurnContract):
        return contract.to_dict()
    if isinstance(contract, dict):
        return {k: contract[k] for k in contract if k in TurnContract.__dataclass_fields__}
    return {}
