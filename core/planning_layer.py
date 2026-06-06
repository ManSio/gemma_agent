"""
Tool-Planning 2.0 — batch tool-calls, dependency checking, sequential execution.
Maps reasoning_state to an execution plan with batched tool calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

PLANNING_LAYER_VERSION = "2.0.0"


PLAN_MODES = ("just_answer", "use_tool", "use_goal")


TOOL_CHAINS: Dict[str, List[str]] = {
    "vision_ocr": ["vision_ocr", "document_reader"],
    "corpus_search": ["corpus_search", "document_reader"],
    "download": ["download", "vision_ocr"],
}

# Tool dependency graph: tool → requires output from
TOOL_DEPENDENCIES: Dict[str, List[str]] = {
    "document_reader": ["vision_ocr", "corpus_search", "download"],
}


@dataclass
class ExecutionPlan:
    mode: str
    intent: str
    topic: str
    reason: str
    # Tool-Planning 2.0: batched tool calls
    tool_chain: List[str] = field(default_factory=list)
    tool_calls_batch: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "intent": self.intent,
            "topic": self.topic,
            "reason": self.reason,
            "tool_chain": list(self.tool_chain),
            "tool_calls_batch": list(self.tool_calls_batch),
        }


@dataclass
class ExecutionStep:
    type: str
    tool: str
    args: Dict[str, Any]


def check_tool_dependencies(tool_calls: List[Dict[str, Any]]) -> bool:
    """Check that all tool dependencies are satisfied within the batch.
    Dependencies are OR-based: any one matching dep in the batch is sufficient."""
    tools_in_batch = {tc.get("tool", "") for tc in tool_calls}
    for tc in tool_calls:
        tool_name = tc.get("tool", "")
        deps = TOOL_DEPENDENCIES.get(tool_name, [])
        if deps and not any(d in tools_in_batch for d in deps):
            return False
    return True


def resolve_tool_order(tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort tool calls so dependencies come first."""
    if not tool_calls or len(tool_calls) <= 1:
        return list(tool_calls)

    # Simple topological sort: tools with no deps first
    ordered = []
    remaining = list(tool_calls)

    while remaining:
        placed = False
        for tc in list(remaining):
            tool_name = tc.get("tool", "")
            deps = TOOL_DEPENDENCIES.get(tool_name, [])
            ordered_tool_names = {o.get("tool", "") for o in ordered}
            if all(d in ordered_tool_names for d in deps):
                ordered.append(tc)
                remaining.remove(tc)
                placed = True
                break
        if not placed:
            # Cycle or all remaining have unmet deps — append as-is
            ordered.extend(remaining)
            break

    return ordered


def build_plan(reasoning_state: Dict[str, Any]) -> ExecutionPlan:
    intent = str(reasoning_state.get("intent") or "chitchat")

    # Self-healing: if previous tool call errored out, fall back to just_answer
    _last_error = reasoning_state.get("last_error")
    if _last_error and isinstance(_last_error, dict) and intent == "direct_tool_action":
        tool_name = _last_error.get("tool", "?")
        return ExecutionPlan(
            mode="just_answer",
            intent=intent,
            topic=str(reasoning_state.get("topic") or ""),
            reason=f"self_healing_after_{tool_name}",
        )

    # Tool-Planning 2.0: check for batched tool calls from reasoning state
    _tool_calls = reasoning_state.get("tool_calls_batch")
    if _tool_calls and isinstance(_tool_calls, list) and len(_tool_calls) > 0:
        if check_tool_dependencies(_tool_calls):
            ordered = resolve_tool_order(_tool_calls)
            return ExecutionPlan(
                mode="use_tool",
                intent=intent,
                topic=str(reasoning_state.get("topic") or ""),
                reason="tool_batch",
                tool_calls_batch=ordered,
            )

    # Tool-chain: for direct_tool_action with a known chain
    if intent == "direct_tool_action":
        _canonical = reasoning_state.get("canonical_tool") or reasoning_state.get("topic", "")
        if str(_canonical).strip() in TOOL_CHAINS:
            chain = TOOL_CHAINS[str(_canonical).strip()]
            return ExecutionPlan(
                mode="use_tool",
                intent="direct_tool_action",
                topic=str(_canonical),
                reason="tool_chain_2_hop",
                tool_chain=list(chain),
            )

    # direct_action: force just_answer, no tools, no goal runner
    if intent == "direct_action":
        return ExecutionPlan(
            mode="just_answer",
            intent="direct_action",
            topic=str(reasoning_state.get("topic") or ""),
            reason=str(reasoning_state.get("reason") or ""),
        )

    # direct_tool_action: force use_tool, no goal runner
    if intent == "direct_tool_action":
        return ExecutionPlan(
            mode="use_tool",
            intent="direct_tool_action",
            topic=str(reasoning_state.get("topic") or ""),
            reason=str(reasoning_state.get("reason") or ""),
        )

    # goal: planning mode
    if intent == "goal":
        return ExecutionPlan(
            mode="use_goal",
            intent="goal",
            topic=str(reasoning_state.get("topic") or ""),
            reason=str(reasoning_state.get("reason") or ""),
        )

    # chitchat or fallback: just_answer
    return ExecutionPlan(
        mode="just_answer",
        intent="chitchat",
        topic=str(reasoning_state.get("topic") or ""),
        reason=str(reasoning_state.get("reason") or ""),
    )
