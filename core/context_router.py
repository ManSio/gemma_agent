"""
Simple action router based on intent classification.
Maps intents to plan modes: just_answer / use_tool / use_goal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

CONTEXT_ROUTER_VERSION = "1.2.0"

ROUTING_TABLE: Dict[str, str] = {
    "direct_action": "just_answer",
    "direct_tool_action": "use_tool",
    "goal": "use_goal",
    "chitchat": "just_answer",
}


@dataclass
class RoutingDecision:
    mode: str
    intent: str
    topic: str
    should_call_tool: bool
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "intent": self.intent,
            "topic": self.topic,
            "should_call_tool": self.should_call_tool,
            "reason": self.reason,
        }


def route(intent_result: Dict[str, Any]) -> RoutingDecision:
    intent = str(intent_result.get("intent") or "chitchat")

    mode = ROUTING_TABLE.get(intent, "just_answer")

    return RoutingDecision(
        mode=mode,
        intent=intent,
        topic=str(intent_result.get("topic") or ""),
        should_call_tool=bool(intent_result.get("should_call_tool")),
        reason=str(intent_result.get("reason") or ""),
    )


