"""
Context Collapse Engine — keeps prompt tokens within budget by summarizing
old history, shrinking large documents, and dropping irrelevant messages.
Controlled by token_efficiency.yml (token_efficiency.collapse).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from core.brain.prompt_pack import estimate_tokens_approx
from core.token_efficiency import (
    collapse_enabled,
    collapse_max_prompt_tokens,
    collapse_history_window,
)

logger = logging.getLogger(__name__)

CONTEXT_COLLAPSE_VERSION = "2.0.0"

_MAX_SUMMARY_TOKENS = 300

# Keys that hold subject-object references and should be cleared on collapse
_SUBJECT_OBJECT_KEYS = frozenset({
    "subject_context", "bound_object", "active_document",
    "subject_context_refs",
})

# Keys that hold old documents and should be truncated
_OLD_DOCUMENT_KEYS = frozenset({
    "document_intake_block", "document_text", "document_body",
})

# Keys that hold reasoning state and should be cleared
_REASONING_STATE_KEYS = frozenset({
    "reasoning_state", "reasoning_plan", "reasoning_chain",
})


def _compact_message(m: Any) -> str:
    if isinstance(m, str):
        return m[:200]
    if isinstance(m, dict):
        role = str(m.get("role", "user"))[:12]
        content = str(m.get("content", ""))[:200]
        return f"[{role}] {content}"
    return str(m)[:200]


def _summarize_dialogue(messages: List[Any], max_summary_chars: int = 600) -> str:
    """Extremely lightweight summary: concatenate first N chars of each message."""
    if not messages:
        return ""
    first = messages[0] if messages else ""
    last = messages[-1] if len(messages) > 1 else ""
    first_s = _compact_message(first)
    last_s = _compact_message(last)
    if len(messages) <= 2:
        return f"{len(messages)} messages: {first_s}"
    return f"{len(messages)} messages: [{first_s} … {last_s}]"


def _summarize_document(text: str, max_chars: int = 400) -> str:
    """Shrink a large document to a short summary (first N chars)."""
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half].rstrip() + " … " + text[-half:].lstrip()


def _estimate_context_tokens(parts: Dict[str, Any]) -> int:
    """Rough estimate of total prompt tokens from context parts."""
    total = 0
    for k, v in parts.items():
        if isinstance(v, str):
            total += max(1, len(v) // 4)
        elif isinstance(v, (list, tuple)):
            for item in v:
                total += max(1, len(str(item)) // 4)
        elif isinstance(v, dict):
            for sub_k, sub_v in v.items():
                total += max(1, len(str(sub_v)) // 4)
    return max(1, total)


def collapse_context(
    *,
    prompt: str,
    est_tokens: int,
    parts: Dict[str, Any],
    recent_dialogue: Optional[List[Any]] = None,
    message_archive: Optional[List[Any]] = None,
    document_intake_block: Optional[str] = None,
    dialogue_summary: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    If est_tokens exceeds the budget, apply collapse:
    - Summarize old dialogue into 1-2 compressed messages
    - Shrink large documents
    - Drop non-essential parts of the context
    Returns (modified_prompt, collapse_meta).
    """
    if not collapse_enabled():
        return prompt, {"collapsed": False}

    max_tokens = collapse_max_prompt_tokens()
    if est_tokens <= max_tokens:
        return prompt, {"collapsed": False, "est_tokens": est_tokens, "budget": max_tokens}

    collapse_meta: Dict[str, Any] = {
        "collapsed": True,
        "est_tokens_before": est_tokens,
        "budget": max_tokens,
    }

    # Summarize recent dialogue
    if recent_dialogue and isinstance(recent_dialogue, list) and len(recent_dialogue) > 3:
        summary = _summarize_dialogue(recent_dialogue)
        collapse_meta["dialogue_summarized"] = True
        collapse_meta["dialogue_messages_before"] = len(recent_dialogue)
        # Inject summary into parts
        parts["dialogue_summary_collapsed"] = summary

    # Summarize message archive
    if message_archive and isinstance(message_archive, list) and len(message_archive) > 6:
        window = collapse_history_window()
        parts["message_archive"] = message_archive[-window:]
        collapse_meta["archive_truncated"] = True
        collapse_meta["archive_before"] = len(message_archive)
        collapse_meta["archive_after"] = len(parts["message_archive"])

    # Shrink large documents
    if document_intake_block and isinstance(document_intake_block, str):
        tok = max(1, len(document_intake_block) // 4)
        if tok > 400:
            parts["document_intake_block"] = _summarize_document(document_intake_block)
            collapse_meta["document_shrunk"] = True

    # Drop non-essential keys
    _non_essential = {
        "topic_tracking", "group_context", "telegram_commands_catalog",
        "tcmd_cat", "plugin_manifest_prompts", "sess_first", "pteacher",
        "ephemeral_lessons", "group_chat_addon",
    }
    dropped: List[str] = []
    for k in _non_essential:
        if k in parts:
            dropped.append(k)
            parts[k] = ""
    if dropped:
        collapse_meta["keys_dropped"] = dropped

    # ── Collapse safety: clear subject-objects, old documents, reasoning state ──
    _subject_cleared: List[str] = []
    for k in _SUBJECT_OBJECT_KEYS:
        if k in parts:
            _subject_cleared.append(k)
            parts[k] = ""
    if _subject_cleared:
        collapse_meta["subject_objects_cleared"] = _subject_cleared

    _docs_cleared: List[str] = []
    for k in _OLD_DOCUMENT_KEYS:
        if k in parts and isinstance(parts.get(k), str) and len(str(parts[k])) > 2000:
            parts[k] = _summarize_document(str(parts[k]), max_chars=300)
            _docs_cleared.append(k)
    if _docs_cleared:
        collapse_meta["old_documents_truncated"] = _docs_cleared

    _reasoning_cleared: List[str] = []
    for k in _REASONING_STATE_KEYS:
        if k in parts:
            _reasoning_cleared.append(k)
            parts[k] = ""
    if _reasoning_cleared:
        collapse_meta["reasoning_state_cleared"] = _reasoning_cleared

    # Clear memory-recall and subject-context from dialog_state if present
    try:
        from core.dialog_state import (
            reset_dialog_state as _ds_reset,
        )
        from core.context_binding import ContextBinder

        _ds_reset("collapse_overflow", user_id="anon", group_id=None)
        collapse_meta["dialog_state_reset"] = True
    except Exception as e:
        logger.debug('%s optional failed: %s', 'context_collapse', e, exc_info=True)
    try:
        from core.reasoning_layer import reset_chain as _reset_chain
        _reset_chain("collapse_overflow")
        collapse_meta["reasoning_chain_reset"] = True
    except Exception as e:
        logger.debug('%s optional failed: %s', 'context_collapse', e, exc_info=True)
    # Re-assemble prompt text (crude: rebuild from parts)
    try:
        from core.brain.prompt_pack import assemble_brain_user_prompt
        from core.prompt_assembly import PromptAssemblyTier

        # Use a simple text-only assembly (we can't know tier here; use FULL)
        collapsed_prompt = _rebuild_prompt_from_parts(parts, prompt)
    except Exception:
        collapsed_prompt = prompt

    collapse_meta["est_tokens_after"] = estimate_tokens_approx(collapsed_prompt)
    logger.info(
        "[collapse] tokens_before=%d tokens_after=%d budget=%d",
        est_tokens,
        collapse_meta.get("est_tokens_after", 0),
        max_tokens,
    )

    # If collapse didn't reduce context enough, trigger forced reset
    _after = collapse_meta.get("est_tokens_after", 0)
    if _after >= est_tokens * 0.9 and est_tokens > max_tokens:
        collapse_meta["forced_reset"] = True
        logger.warning(
            "[collapse] forced_reset — collapse ineffective (before=%d after=%d budget=%d)",
            est_tokens, _after, max_tokens,
        )
        # Force-reset all state subjects
        try:
            from core.reasoning_layer import reset_chain as _reset_chain
            _reset_chain("collapse_ineffective")
        except Exception as e:
            logger.debug('%s optional failed: %s', 'context_collapse', e, exc_info=True)
    return collapsed_prompt, collapse_meta


def _rebuild_prompt_from_parts(parts: Dict[str, Any], original_prompt: str) -> str:
    """Simplified prompt assembly from parts when full assembly is not available."""
    lines: List[str] = []
    for k, v in parts.items():
        if not v:
            continue
        if k.startswith("__"):
            continue
        vs = str(v)
        if len(vs) > 6000:
            vs = vs[:6000] + "…"
        lines.append(f"{k}: {vs}")
    body = "\n".join(lines)
    # Try to extract the static head from the original prompt
    head_end = original_prompt.find("Сообщение пользователя:")
    if head_end < 0:
        head_end = original_prompt.find("User message:")
    if head_end > 0:
        return original_prompt[:head_end] + "\n" + body
    return body
