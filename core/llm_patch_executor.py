"""
Autonomous Patch Executor — classifies repeated errors, generates patches,
presents to admin, and waits for confirmation.
v1.0.0
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

from core.tool_router import (
    queue_patch_for_confirmation,
    get_pending_patch,
    confirm_patch,
    reject_patch,
    list_pending_patches,
)

logger = logging.getLogger(__name__)

LLM_PATCH_EXECUTOR_VERSION = "1.0.0"

_ERROR_HISTORY: List[Dict[str, Any]] = []


def _b(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def is_enabled() -> bool:
    return _b("SELF_MAINTENANCE_ENABLED", True) and _b("AUTONOMY_LAYER_ENABLED", True)


def get_passport_path() -> str:
    p = (os.getenv("DEVELOPMENT_PASSPORT_PATH") or "").strip()
    return p if p else "data/development_passport.json"


def classify_repeated_errors(history: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Classify repeated errors from LLM history. Returns error class dict or None."""
    if not isinstance(history, list) or len(history) < 2:
        _ERROR_HISTORY.append({"ts": time.time(), "history_len": len(history) if history else 0})
        return None
    _ERROR_HISTORY.append({"ts": time.time(), "history_len": len(history)})
    if len(_ERROR_HISTORY) > 100:
        _ERROR_HISTORY[:] = _ERROR_HISTORY[-50:]

    error_types: Dict[str, int] = {}
    for entry in history[-10:]:
        if not isinstance(entry, dict):
            continue
        err = str(entry.get("error") or entry.get("reason") or "")
        if err:
            error_types[err] = error_types.get(err, 0) + 1

    for err_type, count in error_types.items():
        if count >= 3:
            return {
                "type": "repeated_error",
                "error_pattern": err_type,
                "occurrences": count,
                "total_errors": sum(error_types.values()),
            }
    return None


def generate_patch(diff_context: Dict[str, Any]) -> Dict[str, Any]:
    """Generate a patch from error diagnostics context."""
    issue_type = str(diff_context.get("error_pattern") or diff_context.get("type") or "unknown")
    occurrences = int(diff_context.get("occurrences") or 0)

    patch_id = f"auto_{issue_type}_{int(time.time())}"

    patch = {
        "patch_id": patch_id,
        "issue": diff_context,
        "action": "patch_generated",
        "strategy": determine_patch_strategy(issue_type, occurrences),
        "ts": time.time(),
    }

    queue_patch_for_confirmation(
        patch_id=patch_id,
        issue=diff_context,
        patch=patch,
        diff=format_patch_diff(patch),
        tool_name="llm_patch_executor",
    )

    return patch


def present_patch_to_admin(patch: Dict[str, Any]) -> str:
    """Format patch for admin presentation."""
    lines = [
        "=== Автономный патч ===",
        f"ID: {patch.get('patch_id', '?')}",
        f"Тип: {patch.get('issue', {}).get('type', '?')}",
        f"Стратегия: {patch.get('strategy', '?')}",
        f"Ошибка: {patch.get('issue', {}).get('error_pattern', '?')}",
        f"Повторений: {patch.get('issue', {}).get('occurrences', 0)}",
    ]
    return "\n".join(lines)


def wait_for_confirmation(patch_id: str, timeout_sec: float = 600.0) -> bool:
    """Wait for admin confirmation. Returns True if confirmed."""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        entry = get_pending_patch(patch_id)
        if entry and entry.get("confirmed"):
            confirm_patch(patch_id)
            return True
        time.sleep(5.0)  # sync polling: runs in separate thread, not event loop
    reject_patch(patch_id)
    return False


def determine_patch_strategy(issue_type: str, occurrences: int) -> str:
    strategies = {
        "repeated_error": "reduce_reasoning_depth",
        "runaway_latency": "switch_to_free_model",
        "runaway_repetition": "add_stop_sequence",
        "invalid_json": "add_json_format_prompt",
        "tool_error": "disable_failing_tool",
        "bad_routing_empty": "fallback_to_general",
        "bad_routing_confused": "simplify_prompt",
    }
    return strategies.get(issue_type, "reduce_reasoning_depth")


def format_patch_diff(patch: Dict[str, Any]) -> str:
    issue = patch.get("issue", {})
    lines = [
        f"Patch: {patch.get('patch_id', '?')}",
        f"Strategy: {patch.get('strategy', '?')}",
    ]
    if issue.get("error_pattern"):
        lines.append(f"  Error pattern: {issue['error_pattern']}")
    if issue.get("occurrences"):
        lines.append(f"  Occurrences: {issue['occurrences']}")
    return "\n".join(lines)


def get_error_history() -> List[Dict[str, Any]]:
    return list(_ERROR_HISTORY)
