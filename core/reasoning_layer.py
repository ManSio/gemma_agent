"""
Reasoning layer: combines intent classification + action routing
into a compact reasoning_state dict. Pure-heuristic, no LLM call.

Autonomy 3.0: Adaptive Reasoning Depth — three levels (shallow/medium/deep)
with automatic selection based on query complexity.
"""

from __future__ import annotations

import contextvars
import logging
from typing import Any, Dict, List, Optional

import time

from core.semantic_intent import classify_intent
from core.context_router import route, RoutingDecision
from core.context_binding import BoundObject
from core.safety_config import reasoning_reset_enabled, max_reasoning_ms

REASONING_LAYER_VERSION = "2.1.0"

_reasoning_chain: list = []
_reasoning_start_ts_ctx: contextvars.ContextVar[float] = contextvars.ContextVar("reasoning_start_ts", default=0.0)

logger = logging.getLogger(__name__)


def _estimate_complexity(user_text: Optional[str]) -> Dict[str, Any]:
    """Estimate query complexity for adaptive depth selection.

    Factors:
    - query length
    - number of distinct objects/entities
    - presence of goal phrasing
    - syntactic complexity markers
    """
    if not user_text:
        return {"depth": "shallow", "score": 0}
    text = user_text.strip()
    score = 0

    # Length-based
    length = len(text)
    if length > 200:
        score += 3
    elif length > 80:
        score += 1

    # Object count (heuristic: nouns, nouns after prepositions)
    words = text.lower().split()
    if len(words) > 15:
        score += 2
    elif len(words) > 5:
        score += 1

    # Goal markers
    goal_markers = {"хочу", "нужно", "надо", "необходимо", "реализовать", "построить",
                    "создать", "разработать", "спроектировать", "архитектура"}
    for gm in goal_markers:
        if gm in text.lower():
            score += 2
            break

    # Syntactic complexity: conjunctions, nested clauses
    complex_markers = {"если", "когда", "потому что", "поскольку", "чтобы", "после того как",
                       "в случае", "при условии", "с учётом"}
    for cm in complex_markers:
        if cm in text.lower():
            score += 1
            break

    # Determine depth
    if score >= 5:
        depth = "deep"
    elif score >= 2:
        depth = "medium"
    else:
        depth = "shallow"

    return {"depth": depth, "score": score, "length": length, "words": len(words)}


def select_reasoning_depth(user_text: Optional[str]) -> str:
    """Select reasoning depth: shallow, medium, or deep."""
    return _estimate_complexity(user_text)["depth"]


def run_reasoning(
    *,
    user_text: Optional[str] = None,
    bound_object: Optional[BoundObject] = None,
) -> Dict[str, Any]:
    """
    Execute compact reasoning:
      1. if bound_object exists (pronoun resolved) → force direct_tool_action
         UNLESS it's a subject binding (я, мне, меня, ...) — then treat as user-facing.
      2. classify intent via heuristic
      3. estimate complexity for adaptive depth
      4. route to action mode

    Returns reasoning_state dict with keys:
      mode, intent, topic, should_call_tool, reason, depth
    """
    complexity = _estimate_complexity(user_text)

    # Subject binding: user pronouns (я, мне, меня, мой, ...) → treat as user-facing,
    # not as tool-action on the last media object.
    if bound_object is not None and bound_object.type == "subject":
        intent_result = classify_intent(user_text=user_text)
        decision: RoutingDecision = route(intent_result)

        if decision.intent in ("general", "direct_action"):
            return {
                "mode": "just_answer",
                "intent": decision.intent,
                "topic": decision.topic,
                "should_call_tool": False,
                "reason": f"subject_{decision.reason}",
                "bound_object": bound_object.to_dict(),
                "depth": complexity["depth"],
                "complexity_score": complexity["score"],
            }

        return {
            "mode": decision.mode,
            "intent": decision.intent,
            "topic": decision.topic,
            "should_call_tool": decision.should_call_tool,
            "reason": f"subject_{decision.reason}",
            "bound_object": bound_object.to_dict(),
            "depth": complexity["depth"],
            "complexity_score": complexity["score"],
        }

    # If a pronoun was resolved to a bound object, skip classification
    if bound_object is not None:
        topic = bound_object.title or f"{bound_object.type}:{bound_object.id}"
        return {
            "mode": "use_tool",
            "intent": "direct_tool_action",
            "topic": topic,
            "should_call_tool": True,
            "reason": f"bound_{bound_object.type}",
            "bound_object": bound_object.to_dict(),
            "depth": complexity["depth"],
            "complexity_score": complexity["score"],
        }

    intent_result = classify_intent(user_text=user_text)

    decision: RoutingDecision = route(intent_result)

    return {
        "mode": decision.mode,
        "intent": decision.intent,
        "topic": decision.topic,
        "should_call_tool": decision.should_call_tool,
        "reason": decision.reason,
        "depth": complexity["depth"],
        "complexity_score": complexity["score"],
    }


def reset_chain(reason: str = "") -> None:
    """Reset the reasoning chain.

    Called on topic change, noise sequence, collapse overflow,
    or runaway reasoning.
    """
    global _reasoning_chain
    _reasoning_chain.clear()
    _reasoning_start_ts_ctx.set(0.0)
    if reasoning_reset_enabled():
        logger.info("reasoning_layer: chain reset reason=%s", reason or "unspecified")


def start_reasoning_timer() -> None:
    """Record start time for reasoning time limit."""
    _reasoning_start_ts_ctx.set(time.time())


def reasoning_exceeded_time() -> bool:
    """Check if reasoning has exceeded max_reasoning_ms."""
    start_ts = _reasoning_start_ts_ctx.get()
    if start_ts <= 0:
        return False
    elapsed = (time.time() - start_ts) * 1000
    return elapsed > max_reasoning_ms()


def abort_reasoning() -> Dict[str, Any]:
    """Abort reasoning and return a fallback short-answer state."""
    logger.warning("reasoning_layer: aborting — time limit exceeded")
    reset_chain("timeout")
    return {
        "mode": "just_answer",
        "intent": "chitchat",
        "topic": "",
        "should_call_tool": False,
        "reason": "reasoning_timeout",
        "depth": "shallow",
        "complexity_score": 0,
    }


def get_reasoning_chain() -> list:
    return list(_reasoning_chain)
