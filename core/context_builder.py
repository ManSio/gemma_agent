from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from core.token_efficiency import delta_enabled, delta_min_change_chars


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)


class ContextBuilder:
    """Unified context layer for orchestrator/skills/brain.
    v3.0.0: Context Stitching — separates context into static-head, rolling-tail, ephemeral-context.
    """

    def __init__(self):
        self._delta_enabled = delta_enabled()
        self._delta_min_chars = delta_min_change_chars()
        self._stitching_enabled = True
        # Cached static-head state
        self._static_head_cache: Optional[Dict[str, Any]] = None
        self._static_head_hash: str = ""
        self._rolling_tail: List[Dict[str, Any]] = []
        self._rolling_max: int = 20

    # ── Context Stitching (Cursor-style) ──

    def build_stitched(
        self,
        *,
        system_prompt: str = "",
        rules: Optional[List[str]] = None,
        tools_declaration: str = "",
        policy: Optional[Dict[str, Any]] = None,
        persona: Optional[Dict[str, Any]] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        ephemeral: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Build context with Cursor-style stitching:
        - static_head: system prompt, rules, tools, policy, persona
        - rolling_tail: last 10-20 messages
        - ephemeral_context: temporary data, tool results
        """
        # Compute static-head hash to detect changes
        head_parts = {
            "system_prompt": system_prompt or "",
            "rules": sorted(rules or []),
            "tools_declaration": tools_declaration or "",
            "policy": (policy or {}),
            "persona": (persona or {}),
        }
        head_hash = _compute_head_hash(head_parts)

        # Only rebuild static-head when it actually changed
        if self._static_head_cache is None or self._static_head_hash != head_hash:
            self._static_head_cache = dict(head_parts)
            self._static_head_hash = head_hash

        # Rolling tail: keep last N messages
        if messages:
            self._rolling_tail = list(messages[-self._rolling_max:])
        else:
            self._rolling_tail = []

        return {
            "context_version": "2.0",
            "stitching": {
                "enabled": self._stitching_enabled,
                "static_head_hash": self._static_head_hash[:12],
            },
            "static_head": dict(self._static_head_cache or {}),
            "rolling_tail": list(self._rolling_tail),
            "ephemeral_context": dict(ephemeral or {}),
        }

    def clear_stitching(self) -> None:
        self._static_head_cache = None
        self._static_head_hash = ""
        self._rolling_tail.clear()

    # ── Legacy full build (for backward compatibility) ──

    def build(
        self,
        *,
        user_id: Optional[str],
        group_id: Optional[str],
        input_meta: Dict[str, Any],
        persisted: Dict[str, Any],
        persona: Dict[str, Any],
        psychology: Dict[str, Any],
        digital_twin: Dict[str, Any],
        behavior_policy: Optional[Dict[str, Any]] = None,
        knowledge_hint: Optional[Dict[str, Any]] = None,
        predictive_hint: Optional[Dict[str, Any]] = None,
        goal_hints: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        parts = {
            "context_version": "1.0",
            "identity": {
                "user_id": user_id,
                "group_id": group_id,
            },
            "input": {
                "meta": input_meta or {},
                "file_context": (input_meta or {}).get("file_context", {}),
                "document_intake": (input_meta or {}).get("document_intake", {}),
                "code_intake": (input_meta or {}).get("code_intake", {}),
            },
            "user": {
                "persona": persona or {},
                "psychology": psychology or {},
                "facts": (persisted or {}).get("user_facts", {}),
                "facts_meta": (persisted or {}).get("user_facts_meta", {}),
                "digital_twin": digital_twin or {},
            },
            "conversation": {
                "dialogue_state": (persisted or {}).get("dialogue_state", {}),
                "group_context": (persisted or {}).get("group_context", {}),
                "topic_tracking": (persisted or {}).get("topic_tracking", {}),
                "recent_messages": (persisted or {}).get("recent_messages", []),
            },
            "runtime": {
                "behavior_engine": {
                    "last_micro_emotion": (persisted or {}).get("last_micro_emotion", {}),
                    "persona_style_anchor": (persisted or {}).get("persona_style_anchor", {}),
                },
                "behavior_policy": behavior_policy or {},
                "knowledge_hint": knowledge_hint or {},
                "predictive_hint": predictive_hint or {},
                "goal_hints": goal_hints or {},
            },
        }

        # Try stitching first
        if self._stitching_enabled:
            stitched = self.build_stitched(
                system_prompt=str((persisted or {}).get("system_prompt", "")),
                rules=None,
                tools_declaration="",
                policy=(behavior_policy or {}),
                persona=(persona or {}),
                messages=(persisted or {}).get("recent_messages"),
                ephemeral={
                    "file_context": (input_meta or {}).get("file_context", {}),
                    "document_intake": (input_meta or {}).get("document_intake", {}),
                    "code_intake": (input_meta or {}).get("code_intake", {}),
                },
            )
            parts["stitched"] = stitched

        return parts

    def delta_context(
        self,
        current_parts: Dict[str, Any],
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        If delta_enabled, diff against the stored snapshot.
        Returns (is_delta, context_diff_or_full).
        When changes are small (< min_change_chars), returns only the diff.
        Otherwise returns the full context.
        """
        if not self._delta_enabled:
            return False, current_parts
        try:
            from core.context_snapshot import get_context_snapshot

            snap = get_context_snapshot()
            changed, total_changed = snap.diff(current_parts)
            snap.store(current_parts, total_changed)
            if total_changed <= self._delta_min_chars and changed:
                changed["__delta__"] = True
                changed["__delta_total_chars__"] = total_changed
                return True, changed
            return False, current_parts
        except Exception:
            return False, current_parts


def _compute_head_hash(head: Dict[str, Any]) -> str:
    raw = _stable_json(head)
    return hashlib.sha256(raw.encode()).hexdigest()
