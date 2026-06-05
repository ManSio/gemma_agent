"""
Fast-path layer: intercepts simple commands before LLM / reasoning / planning.
Returns a decision dict or None.

Version 2.0: integrates auto-tool-resolution (semantic_intent.normalize_tool_name)
and bound-object awareness (context_binding.BoundObject).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from core.context_binding import BoundObject, bound_object_tool_hint

logger = logging.getLogger(__name__)

FAST_PATH_VERSION = "2.3.0"

# Commands that map to use_tool mode
_USE_TOOL_TRIGGERS: Dict[str, str] = {
    "скачай": "download",
    "проверь": "check",
    "tts": "tts",
    "озвучь": "tts",
    "ocr": "ocr",
    "прочитай": "read",
    "открой документ": "open_document",
    "покажи содержимое": "show_content",
    "найди в корпусе": "search_corpus",
}

# Commands that map to just_answer / direct_action
_DIRECT_ACTION_TRIGGERS: tuple[str, ...] = (
    "сгенерируй",
    "с генерируй",
    "создай",
    "нарисуй",
    "переведи",
    "исправь",
)

# Short imperative list (word count < 6, starting with one of these) → fast-path
_SHORT_IMPERATIVES: tuple[str, ...] = (
    "скачай",
    "проверь",
    "сгенерируй",
    "с генерируй",
    "создай",
    "нарисуй",
    "переведи",
    "исправь",
    "tts",
    "озвучь",
    "ocr",
    "прочитай",
    "найди",
    "покажи",
    "загрузи",
    "сохрани",
    "удали",
    "напиши",
    "сделай",
)

# Context-bound short queries that should trigger tool use
_BOUND_CONTEXT_TRIGGERS: tuple[str, ...] = (
    "что там",
    "что внутри",
    "что в нём",
    "что в них",
    "что внутри него",
    "прочитай",
    "покажи",
    "расскажи про него",
    "расскажи про это",
    "опиши его",
    "опиши это",
)


def _fast_path_candidate(
    user_text: Optional[str],
    bound_object: Optional[BoundObject] = None,
) -> Optional[Dict[str, Any]]:
    """
    Check if user_text is a simple command that can be handled without LLM.

    Priority:
      1. Auto-tool-resolution via semantic_intent.normalize_tool_name (synonyms).
      2. Bound-object context triggers (e.g. "что там?" with a bound document).
      3. Short imperatives (< 6 words, starts with imperative).
      4. Existing use_tool / direct_action trigger table.

    Returns:
      { "mode": "use_tool", "tool": "<canonical_name>", "args": "<parsed>" }
      { "mode": "just_answer", "direct_action": True }
      None  — fall through to normal reasoning pipeline
    """
    if not user_text:
        return None

    low = user_text.strip().lower()
    words = low.split()
    word_count = len(words)

    # 1) Auto-tool-resolution via synonym dictionary
    from core.semantic_intent import (
        normalize_tool_name,
        extract_url,
        extract_quoted,
    )

    canonical_tool = normalize_tool_name(user_text)
    if canonical_tool:
        args: Dict[str, Any] = {"raw": user_text}
        if canonical_tool == "url_check":
            url = extract_url(user_text)
            if url:
                args = {"url": url}
        elif canonical_tool == "download":
            url = extract_url(user_text)
            if url:
                args = {"url": url}
        elif canonical_tool in ("document_reader", "corpus_search", "vision_ocr", "tts"):
            query = extract_quoted(user_text) or user_text
            args = {"query": query}
        return {
            "mode": "use_tool",
            "tool": canonical_tool,
            "args": args,
        }

    # 2) Subject context: if bound_object is a subject, fast-path in subject mode
    # — do NOT trigger tool actions on the subject.
    if bound_object is not None and bound_object.type == "subject":
        return {
            "mode": "just_answer",
            "direct_action": False,
            "reason": "subject_context",
            "bound_object": bound_object.to_dict(),
        }

    # 3) Bound-object context triggers (media objects: document, image, file)
    if bound_object is not None:
        for trigger in _BOUND_CONTEXT_TRIGGERS:
            if trigger in low:
                hint = bound_object_tool_hint(bound_object)
                return {
                    "mode": "use_tool",
                    "tool": hint["type"],
                    "args": user_text,
                    "bound_object": bound_object.to_dict(),
                    "tool_hint": hint["tool_hint"],
                }

    # 3) Short imperatives (< 6 words)
    if word_count < 6 and word_count >= 1:
        for imp in _SHORT_IMPERATIVES:
            if low.startswith(imp):
                return {
                    "mode": "use_tool",
                    "tool": imp,
                    "args": low[len(imp):].strip().strip(",.!?"),
                }

    # 4) Check use_tool triggers (longer phrases first to avoid partial matches)
    for phrase, tool_name in sorted(
        _USE_TOOL_TRIGGERS.items(), key=lambda x: -len(x[0])
    ):
        if low.startswith(phrase):
            after = low[len(phrase):].strip().strip(",.!?")
            return {
                "mode": "use_tool",
                "tool": tool_name,
                "args": after,
            }

    # 5) Check just_answer / direct_action triggers
    for trigger in _DIRECT_ACTION_TRIGGERS:
        if low.startswith(trigger):
            return {
                "mode": "just_answer",
                "direct_action": True,
            }

    return None


def fast_path(
    user_text: Optional[str],
    bound_object: Optional[BoundObject] = None,
    *,
    gate_context: Optional[Dict[str, Any]] = None,
    persisted: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """fast_path с context gate (см. heuristic_context_gate)."""
    candidate = _fast_path_candidate(user_text, bound_object)
    if candidate is None:
        return None
    try:
        from core.heuristic_context_gate import should_run_shortcut

        gr = should_run_shortcut(
            "fast_path_tool",
            user_text or "",
            meta=gate_context if isinstance(gate_context, dict) else None,
            persisted=persisted,
            planner_context=gate_context,
            fast_path_candidate=True,
        )
        if not gr.allowed:
            return None
    except Exception as e:
        logger.debug("fast_path gate: %s", e)
    return candidate
