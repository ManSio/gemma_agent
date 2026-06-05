"""
Tool-Guard Layer — intercepts tool calls before execution.
Blocks SelfProgramming by default, validates required arguments,
and blocks tool-calls in fast-path without explicit user request.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from core.safety_config import tool_guard_enabled

logger = logging.getLogger(__name__)

TOOL_GUARD_VERSION = "2.0.0"

_REQUIRED_ARGS: Dict[str, list] = {
    "download": ["url"],
    "url_check": ["url"],
    "document_reader": ["query"],
    "corpus_search": ["query"],
    "vision_ocr": ["query"],
    "tts": ["text", "query"],
    "digital_twin": [],
}


class ToolGuardResult:
    def __init__(self, allowed: bool, reason: str = "", message: str = ""):
        self.allowed = allowed
        self.reason = reason
        self.message = message


def check_tool_call(
    *,
    tool_name: str,
    args: Optional[Dict[str, Any]] = None,
    allow_self_programming: bool = False,
    is_fast_path: bool = False,
    has_explicit_tool_request: bool = False,
) -> ToolGuardResult:
    """Validate a tool call before execution.

    Args:
        tool_name: canonical tool name
        args: tool arguments dict
        allow_self_programming: whether self-programming is explicitly enabled
        is_fast_path: whether call originated from fast-path
        has_explicit_tool_request: whether user explicitly asked for this tool

    Returns:
        ToolGuardResult with allowed/reason/message.
    """
    if not tool_guard_enabled():
        return ToolGuardResult(allowed=True, reason="guard_disabled")

    tn = (tool_name or "").strip()
    if not tn:
        return ToolGuardResult(allowed=False, reason="empty_tool_name", message="Не указан инструмент.")

    # Block SelfProgramming by default
    if tn.startswith("SelfProgramming") and not allow_self_programming:
        return ToolGuardResult(
            allowed=False,
            reason="self_programming_blocked",
            message="Этот инструмент недоступен.",
        )

    # Check required arguments
    missing = _check_missing_args(tn, args or {})
    if missing:
        return ToolGuardResult(
            allowed=False,
            reason="missing_required_args",
            message="Недостаточно данных для вызова инструмента.",
        )

    # Block tool-calls in fast-path without explicit request
    if is_fast_path and not has_explicit_tool_request:
        if tn not in _FAST_PATH_ALWAYS_ALLOWED:
            return ToolGuardResult(
                allowed=False,
                reason="fast_path_no_explicit_request",
                message="Для вызова инструмента нужно явно попросить.",
            )

    return ToolGuardResult(allowed=True, reason="ok")


_FAST_PATH_ALWAYS_ALLOWED = frozenset({"digital_twin"})


# ── Batch Tool Call Validation ──

def check_tool_calls_batch(
    *,
    tool_calls: List[Dict[str, Any]],
    allow_self_programming: bool = False,
    is_fast_path: bool = False,
) -> Dict[str, Any]:
    """Validate a batch of tool calls. Returns {all_allowed, results, blocked_count}."""
    results = []
    blocked_count = 0
    for tc in tool_calls:
        tool_name = str(tc.get("tool") or tc.get("name") or "")
        args = tc.get("args") or tc.get("arguments") or {}
        has_explicit = bool(tc.get("explicit", False))
        gr = check_tool_call(
            tool_name=tool_name,
            args=args if isinstance(args, dict) else {},
            allow_self_programming=allow_self_programming,
            is_fast_path=is_fast_path,
            has_explicit_tool_request=has_explicit,
        )
        results.append({"tool": tool_name, "allowed": gr.allowed, "reason": gr.reason, "message": gr.message})
        if not gr.allowed:
            blocked_count += 1
    return {
        "all_allowed": blocked_count == 0,
        "results": results,
        "blocked_count": blocked_count,
    }


# ── Autonomous Patch Executor Guard ──

_PATCH_QUEUE: Dict[str, Any] = {}
_pending_confirmation: Dict[str, Dict[str, Any]] = {}


def queue_patch_for_confirmation(
    *,
    patch_id: str,
    issue: Dict[str, Any],
    patch: Dict[str, Any],
    diff: str = "",
    tool_name: str = "",
) -> str:
    """Queue a patch for user confirmation. Returns patch_id."""
    pid = patch_id or f"patch_{id(patch)}"
    _PATCH_QUEUE[pid] = {
        "issue": dict(issue),
        "patch": dict(patch),
        "diff": diff,
        "tool_name": tool_name,
        "ts": time.time(),
        "confirmed": False,
    }
    return pid


def get_pending_patch(patch_id: str) -> Optional[Dict[str, Any]]:
    """Get a pending patch by id. Returns None if not found or already confirmed."""
    entry = _PATCH_QUEUE.get(patch_id)
    if entry and not entry.get("confirmed"):
        return dict(entry)
    return None


def confirm_patch(patch_id: str) -> bool:
    """Confirm a patch and remove from queue. Returns True if confirmed."""
    entry = _PATCH_QUEUE.get(patch_id)
    if entry and not entry.get("confirmed"):
        entry["confirmed"] = True
        return True
    return False


def reject_patch(patch_id: str) -> bool:
    """Reject a patch and remove from queue. Returns True if removed."""
    if patch_id in _PATCH_QUEUE:
        del _PATCH_QUEUE[patch_id]
        return True
    return False


def list_pending_patches() -> List[Dict[str, Any]]:
    """List all unconfirmed patches."""
    return [
        {"patch_id": pid, "issue_type": e.get("issue", {}).get("type"),
         "tool_name": e.get("tool_name", ""), "diff": e.get("diff", "")}
        for pid, e in _PATCH_QUEUE.items()
        if not e.get("confirmed")
    ]


def _check_missing_args(tool_name: str, args: Dict[str, Any]) -> list:
    required = _REQUIRED_ARGS.get(tool_name, [])
    if not required:
        return []
    missing = []
    for r in required:
        val = args.get(r)
        if val is None or (isinstance(val, str) and not val.strip()):
            missing.append(r)
    return missing
