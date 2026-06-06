"""
Unified OpenRouter `reasoning` map for chat/completions.

Docs: https://openrouter.ai/docs/guides/best-practices/reasoning-tokens

Pitfalls (prod):
- high/xhigh consumes ~80–95% of max_tokens — bump floor via OPENROUTER_REASONING_HIGH_MIN_MAX_TOKENS.
- Tool/JSON modes may suppress visible reasoning; use exclude=true for TG (internal CoT, content in message).
- Send reasoning only to models that support it (default prefix deepseek/).
- Do not use legacy top-level reasoning_effort (deprecated alias).
"""
from __future__ import annotations

import os
from typing import Any, Dict, FrozenSet, Optional, Set

_VALID_EFFORT = frozenset({"xhigh", "high", "medium", "low", "minimal", "none"})

_DEFAULT_SKIP_TAGS: FrozenSet[str] = frozenset(
    {
        "brain_fast_chitchat",
        "brain_direct_dialog",
        "router_classifier",
        "router",
        "meta_intent",
        "heuristic_uncertain",
        "task_outline",
        "task_scout_plan",
        "connectivity",
        "openrouter_chat",
    }
)


def _truthy(raw: Optional[str], *, default: bool = False) -> bool:
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _truthy_env(name: str, default: bool = False) -> bool:
    return _truthy(os.getenv(name), default=default)


def _parse_int(v: Optional[str], *, low: int, high: int) -> Optional[int]:
    if v is None:
        return None
    try:
        x = int(str(v).strip())
    except ValueError:
        return None
    return max(low, min(high, x))


def _normalize_tag_slug(tag: Optional[str]) -> str:
    raw = str(tag or "").strip().upper()
    if not raw:
        return ""
    out = []
    for ch in raw:
        if ch.isalnum():
            out.append(ch)
        else:
            out.append("_")
    slug = "".join(out)
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_")


def _env_value_for_tag(tag: Optional[str], key: str) -> Optional[str]:
    slug = _normalize_tag_slug(tag)
    if slug:
        v = os.getenv(f"OPENROUTER_GEN_{slug}_{key}")
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    g = os.getenv(f"OPENROUTER_GEN_{key}")
    if g is None or str(g).strip() == "":
        return None
    return str(g).strip()


def _csv_tag_set(name: str, default: FrozenSet[str]) -> Set[str]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return {t.upper() for t in default}
    return {_normalize_tag_slug(p) for p in raw.split(",") if p.strip()}


def _model_prefixes() -> tuple[str, ...]:
    raw = (os.getenv("OPENROUTER_REASONING_MODEL_PREFIXES") or "deepseek/").strip()
    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    return tuple(parts) if parts else ("deepseek/",)


def _model_allows_reasoning(model: str) -> bool:
    mid = (model or "").strip().lower()
    if not mid:
        return False
    return any(mid.startswith(p) for p in _model_prefixes())


def _is_brain_tag(tag_slug: str) -> bool:
    return bool(tag_slug) and tag_slug.startswith("BRAIN_")


def _tag_only_env(tag: Optional[str], key: str) -> Optional[str]:
    slug = _normalize_tag_slug(tag)
    if not slug:
        return None
    v = os.getenv(f"OPENROUTER_GEN_{slug}_{key}")
    if v is None or str(v).strip() == "":
        return None
    return str(v).strip()


def _resolve_effort(*, tag: Optional[str], tag_slug: str, is_brain: bool) -> Optional[str]:
    tag_effort = _tag_only_env(tag, "REASONING_EFFORT")
    if tag_effort:
        lvl = tag_effort.strip().lower()
        if lvl in _VALID_EFFORT:
            return None if lvl == "none" else lvl

    if is_brain:
        brain = (os.getenv("OPENROUTER_BRAIN_REASONING_EFFORT") or "high").strip().lower()
        if brain in _VALID_EFFORT:
            return None if brain == "none" else brain
        return None

    global_eff = (os.getenv("OPENROUTER_GEN_REASONING_EFFORT") or "").strip().lower()
    if global_eff in _VALID_EFFORT:
        return None if global_eff == "none" else global_eff
    return None


def _resolve_exclude(tag: Optional[str]) -> bool:
    try:
        from core.telegram_stream_reasoning import stream_reasoning_armed
    except ImportError:
        stream_reasoning_armed = lambda: False  # type: ignore[misc, assignment]
    if stream_reasoning_armed():
        raw_vis = _tag_only_env(tag, "REASONING_EXCLUDE")
        if raw_vis is not None:
            return _truthy(raw_vis, default=False)
        return False
    raw = _env_value_for_tag(tag, "REASONING_EXCLUDE")
    if raw is not None:
        return _truthy(raw, default=True)
    return _truthy_env("OPENROUTER_REASONING_EXCLUDE", True)


def _resolve_reasoning_max_tokens(tag: Optional[str]) -> Optional[int]:
    raw = _tag_only_env(tag, "REASONING_MAX_TOKENS")
    return _parse_int(raw, low=16, high=32768)


def _maybe_bump_max_tokens_for_high_effort(payload: Dict[str, Any], effort: str) -> None:
    if effort not in {"high", "xhigh"}:
        return
    floor = _parse_int(
        (os.getenv("OPENROUTER_REASONING_HIGH_MIN_MAX_TOKENS") or "2048").strip(),
        low=512,
        high=32768,
    )
    if floor is None:
        return
    try:
        cur = int(payload.get("max_tokens") or 0)
    except (TypeError, ValueError):
        cur = 0
    if cur > 0 and cur < floor:
        payload["max_tokens"] = floor


def build_reasoning_map(
    *,
    tag: Optional[str],
    model: str,
    max_tokens: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Build OpenRouter unified reasoning block or None if reasoning should be omitted."""
    if not _truthy_env("OPENROUTER_REASONING_ENABLED", True):
        return None
    if not _model_allows_reasoning(model):
        return None

    tag_slug = _normalize_tag_slug(tag)
    skip = _csv_tag_set("OPENROUTER_REASONING_SKIP_TAGS", _DEFAULT_SKIP_TAGS)
    try:
        from core.telegram_stream_reasoning import stream_reasoning_armed
    except ImportError:
        stream_reasoning_armed = lambda: False  # type: ignore[misc, assignment]
    admin_stream = stream_reasoning_armed()
    if tag_slug and tag_slug in skip:
        if not (admin_stream and tag_slug in {"BRAIN_DIRECT_DIALOG", "BRAIN_FIRST"}):
            return None

    enabled_raw = _env_value_for_tag(tag, "REASONING_ENABLED")
    if enabled_raw is not None and not _truthy(enabled_raw, default=False):
        return None

    is_brain = _is_brain_tag(tag_slug)
    reasoning_cap = _resolve_reasoning_max_tokens(tag)
    effort = _resolve_effort(tag=tag, tag_slug=tag_slug, is_brain=is_brain)

    block: Dict[str, Any] = {}
    if reasoning_cap is not None:
        block["max_tokens"] = reasoning_cap
    elif effort:
        block["effort"] = effort
    elif enabled_raw is not None and _truthy(enabled_raw, default=False):
        block["enabled"] = True
    else:
        return None

    block["exclude"] = _resolve_exclude(tag)

    # max_tokens in payload is unused for block build but kept for future guards
    _ = max_tokens
    return block


def apply_openrouter_reasoning(payload: Dict[str, Any], *, tag: Optional[str]) -> Dict[str, Any]:
    """Inject unified reasoning map; strip deprecated reasoning_effort."""
    payload.pop("reasoning_effort", None)
    model = str(payload.get("model") or "").strip()
    try:
        max_tok = int(payload.get("max_tokens")) if payload.get("max_tokens") is not None else None
    except (TypeError, ValueError):
        max_tok = None

    block = build_reasoning_map(tag=tag, model=model, max_tokens=max_tok)
    if not block:
        payload.pop("reasoning", None)
        return payload

    effort = block.get("effort")
    if isinstance(effort, str):
        _maybe_bump_max_tokens_for_high_effort(payload, effort)

    payload["reasoning"] = block
    return payload
