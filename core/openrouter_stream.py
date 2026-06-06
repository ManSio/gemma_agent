"""Разбор SSE-чанков OpenRouter (stream=true)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, List, Optional


@dataclass
class StreamDelta:
    content: str = ""
    reasoning: str = ""


def _text_from_reasoning_details(details: Any) -> str:
    if not isinstance(details, list):
        return ""
    parts: List[str] = []
    for item in details:
        if not isinstance(item, dict):
            continue
        t = str(item.get("text") or item.get("content") or "").strip()
        if t and t != "[REDACTED]":
            parts.append(t)
    return "".join(parts)


def extract_reasoning_from_delta(delta: Any) -> str:
    if not isinstance(delta, dict):
        return ""
    for key in ("reasoning", "reasoning_content"):
        raw = delta.get(key)
        if raw is None:
            continue
        text = str(raw).strip()
        if text and text != "[REDACTED]":
            return text
    rd = _text_from_reasoning_details(delta.get("reasoning_details"))
    if rd:
        return rd
    return ""


def parse_openrouter_sse_chunk(line: str) -> StreamDelta:
    """Content + reasoning из одной SSE строки."""
    raw = (line or "").strip()
    if not raw.startswith("data:"):
        return StreamDelta()
    payload = raw[5:].strip()
    if not payload or payload == "[DONE]":
        return StreamDelta()
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError:
        return StreamDelta()
    if not isinstance(obj, dict):
        return StreamDelta()
    if isinstance(obj.get("error"), dict):
        return StreamDelta()
    choices = obj.get("choices")
    if not isinstance(choices, list) or not choices:
        return StreamDelta()
    ch0 = choices[0]
    if not isinstance(ch0, dict):
        return StreamDelta()
    delta = ch0.get("delta")
    if not isinstance(delta, dict):
        return StreamDelta()
    content = ""
    piece = delta.get("content")
    if piece is not None:
        content = str(piece)
    reasoning = extract_reasoning_from_delta(delta)
    return StreamDelta(content=content, reasoning=reasoning)


def parse_openrouter_sse_data_line(line: str) -> Optional[str]:
    """
    Из строки ``data: {...}`` вернуть delta content или None.
    ``data: [DONE]`` и пустые строки → None.
    """
    chunk = parse_openrouter_sse_chunk(line)
    text = chunk.content
    return text if text else None


def merge_stream_finish_reason(line: str) -> str:
    """finish_reason из финального SSE-чанка (если есть)."""
    raw = (line or "").strip()
    if not raw.startswith("data:"):
        return ""
    payload = raw[5:].strip()
    if not payload or payload == "[DONE]":
        return ""
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError:
        return ""
    if not isinstance(obj, dict):
        return ""
    choices = obj.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    ch0 = choices[0]
    if isinstance(ch0, dict):
        return str(ch0.get("finish_reason") or "").strip().lower()
    return ""
