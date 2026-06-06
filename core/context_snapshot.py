"""
Context Snapshot — stores the last assembled context for delta prompting.
Used by Prompt Delta Engine (token_efficiency.delta_enabled).
v2.0.0: adds Context Stitching data classes (StaticHead, RollingTail, EphemeralContext).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

CONTEXT_SNAPSHOT_VERSION = "2.0.0"


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)


def compute_context_hash(parts: Dict[str, Any]) -> str:
    """Deterministic content hash for a context parts dict."""
    normalized = {k: _stable_json(v) if isinstance(v, (dict, list)) else str(v) for k, v in parts.items()}
    raw = json.dumps(normalized, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class ContextSnapshot:
    """Stores the last assembled context for delta diffing."""

    def __init__(self):
        self._snapshot: Dict[str, Any] = {}
        self._hash: str = ""
        self._prompt_chars: int = 0

    def store(self, parts: Dict[str, Any], prompt_chars: int) -> None:
        self._snapshot = dict(parts)
        self._hash = compute_context_hash(parts)
        self._prompt_chars = prompt_chars

    def get_last(self) -> Dict[str, Any]:
        return dict(self._snapshot)

    def get_hash(self) -> str:
        return self._hash

    def get_prompt_chars(self) -> int:
        return self._prompt_chars

    def diff(self, new_parts: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
        """
        Compare new_parts against stored snapshot.
        Returns (changed_keys_dict, total_changed_chars).
        """
        old = self._snapshot
        changed: Dict[str, Any] = {}
        total_changed = 0

        for k, new_v in new_parts.items():
            old_v = old.get(k)
            if k not in old:
                changed[k] = new_v
                total_changed += len(str(new_v or ""))
            elif _stable_json(old_v) != _stable_json(new_v):
                changed[k] = new_v
                total_changed += len(str(new_v or ""))

        return changed, total_changed

    def clear(self) -> None:
        self._snapshot = {}
        self._hash = ""
        self._prompt_chars = 0


_GLOBAL_SNAPSHOT = ContextSnapshot()


def get_context_snapshot() -> ContextSnapshot:
    return _GLOBAL_SNAPSHOT


# ── Context Stitching Models (v2.0.0) ──

@dataclass
class StaticHead:
    system_prompt: str = ""
    rules: List[str] = field(default_factory=list)
    tools_declaration: str = ""
    policy: Dict[str, Any] = field(default_factory=dict)
    persona: Dict[str, Any] = field(default_factory=dict)
    version_hash: str = ""

    def is_stale(self, current_hash: str) -> bool:
        return self.version_hash != current_hash

    def to_dict(self) -> Dict[str, Any]:
        return {
            "system_prompt": self.system_prompt,
            "rules": list(self.rules),
            "tools_declaration": self.tools_declaration,
            "policy": dict(self.policy),
            "persona": dict(self.persona),
        }


@dataclass
class RollingTail:
    messages: List[Dict[str, Any]] = field(default_factory=list)
    max_messages: int = 20
    turns_since_anchor: int = 0

    def add(self, msg: Dict[str, Any]) -> None:
        self.messages.append(msg)
        if len(self.messages) > self.max_messages:
            self.messages = self.messages[-self.max_messages:]

    def to_list(self) -> List[Dict[str, Any]]:
        return list(self.messages)


@dataclass
class EphemeralContext:
    tool_results: List[Dict[str, Any]] = field(default_factory=list)
    code_intake: Dict[str, Any] = field(default_factory=dict)
    document_intake: Dict[str, Any] = field(default_factory=dict)
    image_context: Dict[str, Any] = field(default_factory=dict)

    def clear(self) -> None:
        self.tool_results.clear()
        self.code_intake = {}
        self.document_intake = {}
        self.image_context = {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_results": list(self.tool_results),
            "code_intake": dict(self.code_intake),
            "document_intake": dict(self.document_intake),
            "image_context": dict(self.image_context),
        }
