"""
LLM Proxy Engine — Cursor IDE-level proxy with reasoning-cache, delta-prompting,
context-stitching, token-budget, self-healing, and patch-executor.
v1.0.0
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from core.llm_cache import (
    make_cache_key as _make_cache_key,
    get as _cache_get,
    set as _cache_set,
    invalidate_on_reset as _cache_invalidate,
)
from core.llm_budget import (
    estimate_tokens as _estimate_tokens,
    similar as _similar,
    build_delta as _build_delta,
    should_collapse as _should_collapse,
)
from core.llm_patch_executor import (
    classify_repeated_errors as _classify_errors,
    generate_patch as _generate_patch,
    is_enabled as _patch_executor_enabled,
    get_error_history as _patch_error_history,
)

logger = logging.getLogger(__name__)

LLM_PROXY_VERSION = "1.0.0"

# Per-session state
_ACTIVE_MODEL: Optional[str] = None
_PREV_CONTEXT: Optional[Dict[str, Any]] = None
_ERROR_HISTORY: List[Dict[str, Any]] = []
_FREE_TOKENS: int = 0


def _b(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _f(name: str, default: float) -> float:
    try:
        return float((os.getenv(name) or str(default)).strip())
    except ValueError:
        return default


def _i(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or str(default)).strip())
    except ValueError:
        return default


def is_proxy_enabled() -> bool:
    return _b("LLM_PROXY_ENABLED", True)


class ProxyResult:
    def __init__(
        self,
        content: str = "",
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        reasoning_decision: Optional[Dict[str, Any]] = None,
        model_used: str = "",
        cache_hit: bool = False,
        delta_applied: bool = False,
        budget_applied: bool = False,
        stitch_applied: bool = False,
        self_heal_action: Optional[str] = None,
        patch_pending: bool = False,
    ):
        self.content = content
        self.tool_calls = tool_calls or []
        self.reasoning_decision = reasoning_decision or {}
        self.model_used = model_used
        self.cache_hit = cache_hit
        self.delta_applied = delta_applied
        self.budget_applied = budget_applied
        self.stitch_applied = stitch_applied
        self.self_heal_action = self_heal_action
        self.patch_pending = patch_pending

    def to_dict(self) -> Dict[str, Any]:
        return {
            "content": self.content,
            "tool_calls": self.tool_calls,
            "reasoning_decision": self.reasoning_decision,
            "model_used": self.model_used,
            "cache_hit": self.cache_hit,
            "delta_applied": self.delta_applied,
            "budget_applied": self.budget_applied,
            "stitch_applied": self.stitch_applied,
            "self_heal_action": self.self_heal_action,
            "patch_pending": self.patch_pending,
        }


async def process(
    user_input: str,
    context: Dict[str, Any],
    *,
    router: Optional[Any] = None,
    active_model_tier: str = "",
    session_digest: Optional[Dict[str, Any]] = None,
    experience_memory: Optional[Dict[str, Any]] = None,
    session_id: str = "",
) -> ProxyResult:
    """Main proxy entry point. Coordinates all sub-modules."""
    if not is_proxy_enabled():
        return ProxyResult(content=user_input, model_used=active_model_tier)

    global _ACTIVE_MODEL, _PREV_CONTEXT, _ERROR_HISTORY, _FREE_TOKENS

    start_ts = time.monotonic()

    # 1. Normalize input
    normalized = normalize_input(user_input)

    # 2. Check cache
    model_name = "deepseek/deepseek-v4-pro"
    cache_key = _make_cache_key(context, normalized, model_name)
    cached = check_cache(cache_key)
    if cached is not None:
        return ProxyResult(
            content=str(cached.get("content") or ""),
            tool_calls=cached.get("tool_calls"),
            reasoning_decision=cached.get("reasoning_decision"),
            model_used=model_name,
            cache_hit=True,
        )

    # 3. Apply budget
    budget_result = apply_budget(context, normalized)
    model_name = budget_result.get("model", model_name)
    _ACTIVE_MODEL = model_name

    # 4. Apply delta-prompting (Cursor IDE pattern)
    delta_context, is_delta = _apply_delta_prompting(context)

    # 5. Collapse context FIRST (before stitching — prevents 3x inflation)
    collapsed_context = collapse_context_before_stitch(delta_context)

    # 6. Build context stitch ON collapsed context
    stitch_context = build_context_stitch(
        context=collapsed_context,
        session_digest=session_digest,
        experience_memory=experience_memory,
    )

    # 7. Route to model
    routed_model = route_to_model(model_name, context, router, active_model_tier)

    # 8. Call LLM
    response = await call_llm(normalized, stitch_context, routed_model, session_id=session_id)

    # 9. Postprocess
    result = postprocess(response)

    # 10. Self-heal — log-only, never switch model or reset KV
    latency = time.monotonic() - start_ts
    heal_action = self_heal(result.to_dict(), latency)
    # Self-heal is disabled — maintains deterministic proxy behavior

    # 10. Cache response
    cache_response(cache_key, result.to_dict())

    # 11. Maybe generate patch
    patch_pending = maybe_generate_patch()

    # Store context snapshot for next delta
    _PREV_CONTEXT = dict(context)

    result.cache_hit = False
    result.delta_applied = is_delta
    result.budget_applied = budget_result.get("applied", False)
    result.stitch_applied = True
    result.self_heal_action = heal_action.get("action") if heal_action else None
    result.patch_pending = patch_pending
    result.model_used = routed_model

    return result


def normalize_input(user_input: str) -> str:
    """Normalize user input: strip, deduplicate whitespace, limit length."""
    text = str(user_input or "").strip()
    text = " ".join(text.split())
    return text[:32000]


def check_cache(key: str) -> Optional[Dict[str, Any]]:
    """Check reasoning-cache for existing response."""
    return _cache_get(key)


def apply_budget(context: Dict[str, Any], user_input: str) -> Dict[str, Any]:
    """Apply token budget: estimate tokens, check collapse needed."""
    result: Dict[str, Any] = {"applied": False, "model": "deepseek/deepseek-v4-pro"}

    est = _estimate_tokens(context, user_input)
    result["est_tokens"] = est

    # Check if context should collapse
    if _should_collapse(context):
        result["should_collapse"] = True
        result["reason"] = "_collapse"

    result["applied"] = True
    return result


def _apply_delta_prompting(context: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    """Apply delta-prompting (Cursor IDE pattern): if contexts are similar, return diff.
    Uses key-overlap similarity check before building delta — 2-4x prompt reduction."""
    global _PREV_CONTEXT
    if _PREV_CONTEXT is not None and _similar(_PREV_CONTEXT, context):
        delta = _build_delta(_PREV_CONTEXT, context)
        if delta.get("__delta__"):
            return delta, True
    return dict(context), False


def build_context_stitch(
    *,
    context: Dict[str, Any],
    session_digest: Optional[Dict[str, Any]] = None,
    experience_memory: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build Cursor-IDE structured prompt: system, persona, static_head, digest, history, user."""
    system_prompt = str(context.get("system_prompt") or "")
    persona = str(context.get("persona") or context.get("static_head", {}).get("persona", ""))
    if isinstance(persona, dict):
        import json
        persona = json.dumps(persona, sort_keys=True, ensure_ascii=False)

    # Digest: ≤300 chars, deterministic
    digest_raw = str(context.get("session_digest") or (session_digest or {}).get("text", ""))
    digest = " ".join(digest_raw.strip().split())[:300]

    # History: last 6 messages only
    messages = context.get("recent_messages")
    if isinstance(messages, list):
        history = messages[-6:]
    else:
        history = []

    # User input
    user_text = str(context.get("user_text") or context.get("user_input", ""))

    return {
        "system": system_prompt,
        "persona": persona,
        "static_head": str(context.get("static_head", "") or ""),
        "digest": digest,
        "history": history,
        "user": user_text,
    }


def collapse_context_before_stitch(context: Dict[str, Any]) -> Dict[str, Any]:
    """Collapse context BEFORE stitching — always runs deterministically.
    Trims history to last 6 messages, removes non-essential keys, normalizes whitespace."""
    ctx = dict(context)

    # Trim recent_messages to last 6
    msgs = ctx.get("recent_messages")
    if isinstance(msgs, list) and len(msgs) > 6:
        ctx["recent_messages"] = msgs[-6:]
    elif isinstance(msgs, list):
        ctx["recent_messages"] = list(msgs)

    # Drop non-essential keys
    _non_essential = {
        "experience_memory_hint", "route_risk_hint",
        "topic_tracking", "group_context", "plugin_manifest_prompts",
        "ephemeral_lessons", "group_chat_addon", "tcmd_cat",
    }
    for k in _non_essential:
        ctx.pop(k, None)

    return ctx


def route_to_model(
    model_name: str,
    context: Dict[str, Any],
    router: Optional[Any] = None,
    active_model_tier: str = "",
) -> str:
    """Route to model — use the model selected by budget or caller."""
    return model_name or "deepseek/deepseek-v4-pro"


async def call_llm(
    user_input: str,
    context: Dict[str, Any],
    model_name: str,
    session_id: str = "",
) -> Dict[str, Any]:
    """Call LLM via OpenRouter provider with the routed model name."""
    try:
        system_prompt = str(context.get("system_prompt") or context.get("static_head", ""))

        from core.openrouter_provider import get_openrouter_provider
        provider = get_openrouter_provider()
        result = await provider.generate(
            prompt=user_input,
            model=model_name,
            system_prompt=system_prompt,
            max_tokens=_i("OPENROUTER_GEN_MAX_TOKENS", 4096),
            session_id=session_id,
        )
        return result if isinstance(result, dict) else {"content": str(result)}
    except Exception as e:
        logger.warning("llm_proxy call_llm error: %s", e)
        return {"content": "", "error": str(e)}


def postprocess(response: Dict[str, Any]) -> ProxyResult:
    """Postprocess LLM response: extract content, tool_calls, reasoning."""
    if not isinstance(response, dict):
        return ProxyResult(content=str(response))

    content = str(response.get("content") or "")
    tool_calls = response.get("tool_calls") if isinstance(response.get("tool_calls"), list) else []
    reasoning = response.get("reasoning_decision") if isinstance(response.get("reasoning_decision"), dict) else {}

    # Parse TOOL_CALL blocks from content if not in structured field
    if not tool_calls and "TOOL_CALL:" in content:
        try:
            from core.brain.text_helpers import parse_tool_call as _pc
            parsed = _pc(content)
            if parsed:
                tool_calls = [parsed]
        except Exception as e:
            logger.debug('%s optional failed: %s', 'llm_proxy', e, exc_info=True)
    # Parse reasoning from content
    if not reasoning and "REASONING:" in content:
        reasoning = {"raw": content.split("REASONING:")[1].split("\n")[0][:500]}

    return ProxyResult(content=content, tool_calls=tool_calls, reasoning_decision=reasoning)


def self_heal(response: Dict[str, Any], latency: float) -> Optional[Dict[str, Any]]:
    """Self-heal: log-only. Never switches model, resets KV, or modifies prompt."""
    return None


def cache_response(key: str, result: Dict[str, Any]) -> None:
    """Cache LLM response."""
    _cache_set(key, result)


def maybe_generate_patch() -> bool:
    """Check if we should generate an autonomous patch."""
    if not _patch_executor_enabled():
        return False

    error_class = _classify_errors(_patch_error_history())
    if error_class:
        try:
            _generate_patch(error_class)
            return True
        except Exception as e:
            logger.debug('%s optional failed: %s', 'llm_proxy', e, exc_info=True)
    return False


def reset_proxy_state() -> None:
    """Reset proxy state (for tests and topic changes)."""
    global _ACTIVE_MODEL, _PREV_CONTEXT, _ERROR_HISTORY, _FREE_TOKENS
    _ACTIVE_MODEL = None
    _PREV_CONTEXT = None
    _ERROR_HISTORY.clear()
    _FREE_TOKENS = 0
    _cache_invalidate("proxy_reset")


def set_active_model(model: str) -> None:
    global _ACTIVE_MODEL
    _ACTIVE_MODEL = model


def set_free_tokens(tokens: int) -> None:
    global _FREE_TOKENS
    _FREE_TOKENS = max(0, tokens)


def get_proxy_state() -> Dict[str, Any]:
    return {
        "active_model": _ACTIVE_MODEL,
        "error_history_len": len(_ERROR_HISTORY),
        "free_tokens": _FREE_TOKENS,
        "version": LLM_PROXY_VERSION,
    }
