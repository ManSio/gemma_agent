"""Ограничение размера результата инструмента перед вторым проходом LLM."""

from __future__ import annotations

import json
import os
from typing import Any


def shrink_tool_result_for_second_stage(tool_name: str, tool_result: Any) -> Any:
    """
    Если сериализованный результат слишком длинный — оставить голову/хвост и метаданные.
    """
    try:
        max_chars = int((os.getenv("BRAIN_SECOND_TOOL_RESULT_MAX_CHARS") or "45000").strip())
    except ValueError:
        max_chars = 45000
    max_chars = max(500, min(max_chars, 500_000))
    try:
        head = int((os.getenv("BRAIN_SECOND_TOOL_RESULT_HEAD_CHARS") or "18000").strip())
    except ValueError:
        head = 18000
    try:
        tail = int((os.getenv("BRAIN_SECOND_TOOL_RESULT_TAIL_CHARS") or "12000").strip())
    except ValueError:
        tail = 12000
    head = max(1000, min(head, max_chars))
    tail = max(500, min(tail, max_chars))

    try:
        raw = json.dumps(tool_result, ensure_ascii=False, default=str)
    except Exception:
        raw = str(tool_result)
    if len(raw) <= max_chars:
        return tool_result
    mid = "\n... [BRAIN_SECOND: результат инструмента обрезан по размеру; уточните запрос или используйте /zip_read bundle.json section=...] ...\n"
    preview = raw[:head] + mid + raw[-tail:]
    return {
        "_brain_second_truncated": True,
        "_tool": tool_name,
        "_approx_json_chars": len(raw),
        "_preview": preview,
    }
