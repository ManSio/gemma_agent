"""Выполнение TOOL_CALL из brain pipeline: args, dedup, run_tool."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional

from core.brain.pipeline_postprocess import emit_brain_tool_finished as _emit_brain_tool_finished
from core.brain.tool_dedup import lookup as _tool_dedup_lookup, store as _tool_dedup_store
from core.brain.tool_result_shrink import shrink_tool_result_for_second_stage
from core.error_analysis import record_error_event
from core.monitoring import MONITOR
from core.tool_args_normalize import normalize_brain_tool_args

logger = logging.getLogger(__name__)

RunToolFn = Callable[..., Awaitable[Any]]


@dataclass
class ToolExecOutcome:
    tool_name: str
    tool_args: Dict[str, Any]
    tool_result: Any


def enrich_tool_args(
    tool_call: Dict[str, Any],
    *,
    user_id: str,
    context: Dict[str, Any],
    user_facts: Dict[str, Any],
    task_facts: Dict[str, Any],
) -> tuple[str, Dict[str, Any]]:
    tool_name = tool_call.get("name")
    if not isinstance(tool_name, str) or not tool_name.strip():
        tool_name = ""
    tool_args = tool_call.get("args", {}) or {}
    if not isinstance(tool_args, dict):
        tool_args = {}

    tid = tool_args.get("user_id")
    if tid is None or (isinstance(tid, str) and not tid.strip()):
        tool_args["user_id"] = user_id

    if task_facts.get("is_weather"):
        if (
            task_facts.get("weather_use_coords")
            and task_facts.get("weather_lat") is not None
            and task_facts.get("weather_lon") is not None
        ):
            tool_args["latitude"] = task_facts.get("weather_lat")
            tool_args["longitude"] = task_facts.get("weather_lon")
        _wx_city = task_facts.get("weather_geo_query") or task_facts.get("weather_city")
        if _wx_city and "city" not in tool_args:
            tool_args["city"] = _wx_city
        elif user_facts.get("city") and "city" not in tool_args:
            tool_args["city"] = user_facts.get("city")
        if task_facts.get("weather_country") and "country" not in tool_args:
            tool_args["country"] = task_facts.get("weather_country")
        elif user_facts.get("country") and "country" not in tool_args:
            tool_args["country"] = user_facts.get("country")

    if task_facts.get("is_time") and user_facts.get("timezone") and "timezone" not in tool_args:
        tool_args["timezone"] = user_facts.get("timezone")
    if task_facts.get("is_currency") and user_facts.get("currency") and "currency" not in tool_args:
        tool_args["currency"] = user_facts.get("currency")
    if task_facts.get("is_currency") and user_facts.get("country") and "country" not in tool_args:
        tool_args["country"] = user_facts.get("country")
    if user_facts.get("language") and "language" not in tool_args:
        tool_args["language"] = user_facts.get("language")

    if isinstance(tool_name, str) and tool_name.startswith("DialogRecall."):
        ctx = context if isinstance(context, dict) else {}
        tool_args["recall_context"] = {
            "dialogue_summary": ctx.get("dialogue_summary") or "",
            "mem0_facts": ctx.get("mem0_facts"),
            "user_facts": dict(user_facts) if isinstance(user_facts, dict) else {},
            "recent_dialogue": ctx.get("recent_dialogue")
            if isinstance(ctx.get("recent_dialogue"), list)
            else ctx.get("recent_messages"),
            "telegram_message_date_unix": ctx.get("telegram_message_date_unix"),
        }
        gid_tool = tool_args.get("group_id")
        if (gid_tool is None or (isinstance(gid_tool, str) and not str(gid_tool).strip())) and ctx.get(
            "group_id"
        ) is not None:
            gs = str(ctx.get("group_id")).strip()
            if gs:
                tool_args["group_id"] = gs

    tool_args = normalize_brain_tool_args(tool_name, tool_args)
    return tool_name, tool_args


async def execute_brain_tool(
    tool_call: Dict[str, Any],
    *,
    user_id: str,
    context: Dict[str, Any],
    user_facts: Dict[str, Any],
    task_facts: Dict[str, Any],
    run_tool: RunToolFn,
) -> ToolExecOutcome:
    tool_name, tool_args = enrich_tool_args(
        tool_call,
        user_id=user_id,
        context=context,
        user_facts=user_facts,
        task_facts=task_facts,
    )

    if not tool_name:
        tool_result: Any = {"error": "invalid tool name"}
    else:
        try:
            cached = _tool_dedup_lookup(user_id, tool_name, tool_args)
            if cached is not None:
                MONITOR.inc("brain_tool_dedup_hit_total")
                tool_result = cached
            else:
                tool_result = await run_tool(tool_name, **tool_args)
                _tool_dedup_store(user_id, tool_name, tool_args, tool_result)
        except Exception as e:
            logger.error("[brain] tool call failed: %s", e)
            record_error_event("brain", "run_tool", exc=e, extra={"tool": tool_name, "user_id": user_id})
            tool_result = {"error": str(e), "tool": tool_name}
        _emit_brain_tool_finished(user_id, context, tool_name, tool_result)

    if isinstance(tool_result, dict) and tool_result.get("error"):
        record_error_event(
            "brain",
            "tool returned error",
            extra={"tool": tool_name, "user_id": user_id, "error": tool_result.get("error")},
        )

    tool_result = shrink_tool_result_for_second_stage(tool_name, tool_result)
    return ToolExecOutcome(tool_name=tool_name, tool_args=tool_args, tool_result=tool_result)
