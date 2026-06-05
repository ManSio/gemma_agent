"""Hermes L1: trim old tool blobs in dialogue history."""
from __future__ import annotations

import os

import pytest


def test_trim_old_tool_blob_keeps_recent(monkeypatch):
    from core.context_tool_trim import trim_tool_outputs_in_dialogue

    monkeypatch.setenv("CONTEXT_TOOL_OUTPUT_TRIM_ENABLED", "true")
    monkeypatch.setenv("CONTEXT_TOOL_OUTPUT_KEEP_RECENT", "1")
    monkeypatch.setenv("CONTEXT_TOOL_OUTPUT_MIN_CHARS", "100")
    big = '{"ok": true, "text": "' + ("x" * 1500) + '"}'
    rows = [
        {"role": "user", "text": "a"},
        {"role": "assistant", "text": big},
        {"role": "user", "text": "b"},
        {"role": "assistant", "text": big},
    ]
    out = trim_tool_outputs_in_dialogue(rows)
    assert out[1].get("_tool_output_trimmed") is True
    assert "сжат" in out[1]["text"] or "placeholder" in out[1]["text"].lower() or "результат" in out[1]["text"]
    assert out[-1]["text"] == big


def test_trim_disabled_passthrough(monkeypatch):
    from core.context_tool_trim import trim_tool_outputs_in_dialogue

    monkeypatch.setenv("CONTEXT_TOOL_OUTPUT_TRIM_ENABLED", "false")
    rows = [{"role": "assistant", "text": '{"ok":1}' + "z" * 2000}]
    assert trim_tool_outputs_in_dialogue(rows) == rows
