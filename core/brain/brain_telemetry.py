"""C6: единая телеметрия brain → context, llm_usage, turns.jsonl."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def brain_recent_limit_for_profile(brain_profile: str) -> int:
    try:
        from core.brain.profile_registry import get_profile

        bp = (brain_profile or "").strip() or "standard"
        return int(getattr(get_profile(bp), "recent_count", 0) or 0)
    except (TypeError, ValueError, AttributeError):
        return 0


def make_brain_telemetry_extra(
    brain_profile: str,
    *,
    prompt_tokens_est: int = 0,
    prompt_chars: int = 0,
    **fields: Any,
) -> Dict[str, Any]:
    lim = brain_recent_limit_for_profile(brain_profile)
    bp = (brain_profile or "").strip() or "standard"
    out: Dict[str, Any] = {
        "brain_recent_limit": lim,
        "brain_profile": bp,
        "prompt_tokens_est": int(prompt_tokens_est or 0),
    }
    if prompt_chars > 0:
        out["prompt_chars"] = int(prompt_chars)
    for k, v in fields.items():
        if v is not None and v != "":
            out[str(k)] = v
    return out


def stash_brain_turn_telemetry(
    context: Optional[Dict[str, Any]],
    *,
    telemetry_extra: Dict[str, Any],
    brain_profile: str,
    brain_recent_limit: int = 0,
) -> None:
    """Снимок для turn.outcome / analyze (не только dialogue_state)."""
    if not isinstance(context, dict):
        return
    lim = int(brain_recent_limit or 0) or brain_recent_limit_for_profile(brain_profile)
    try:
        pt = int((telemetry_extra or {}).get("prompt_tokens_est") or 0)
    except (TypeError, ValueError):
        pt = 0
    bp = (brain_profile or "").strip() or "standard"
    pack = {
        "prompt_tokens_est": pt,
        "brain_recent_limit": lim,
        "brain_profile": bp,
    }
    compaction = (telemetry_extra or {}).get("compaction")
    if isinstance(compaction, dict) and compaction:
        pack["compaction"] = compaction
    context["brain_turn_telemetry"] = pack
    ds = context.setdefault("dialogue_state", {})
    if isinstance(ds, dict):
        if pt > 0:
            ds["prompt_tokens_est"] = pt
        if lim > 0:
            ds["brain_recent_limit"] = lim
        ds["brain_profile"] = bp


def prompt_tokens_est_from_usage(usage: Any, *, prompt: str = "") -> int:
    if isinstance(usage, dict):
        try:
            pt = int(usage.get("prompt_tokens") or 0)
        except (TypeError, ValueError):
            pt = 0
        if pt > 0:
            return pt
    p = (prompt or "").strip()
    return max(1, len(p) // 4) if p else 0
