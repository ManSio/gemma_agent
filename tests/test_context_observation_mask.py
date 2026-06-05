"""Brain context observation masking."""

import os

import pytest

from core.brain.context_observation_mask import (
    mask_external_hint,
    mask_observation_dialogue,
    observation_mask_enabled,
)
from core.openrouter_silent_refusal import (
    completion_looks_like_silent_refusal,
    silent_refusal_fallback_model,
)


def test_mask_old_tool_heavy_assistant():
    rows = [
        {"role": "user", "content": "найди новости"},
        {
            "role": "assistant",
            "content": '{"ok": true, "http_status": 200, "truncated": false, "text": "' + ("x" * 3000) + '"}',
        },
        {"role": "user", "content": "кратко"},
        {"role": "assistant", "content": "Короткий ответ по делу."},
    ]
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("BRAIN_CONTEXT_MASK_KEEP_RECENT_ASSISTANT", "1")
        out = mask_observation_dialogue(rows)
    assert out[1].get("_observation_masked") is True
    assert "сокращён" in str(out[1].get("content") or "")
    assert out[3].get("_observation_masked") is not True


def test_mask_external_hint_middle():
    blocks = ["Clock: 12:00", "A" * 500, "B" * 500, "C" * 500, "tail keep"]
    hint = "\n\n".join(blocks)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("BRAIN_CONTEXT_EXTERNAL_HINT_MAX_CHARS", "1200")
        mp.setenv("BRAIN_CONTEXT_OBSERVATION_MASK_ENABLED", "true")
        short = mask_external_hint(hint)
    assert len(short) < len(hint)
    assert short.startswith("Clock:")


def test_silent_refusal_detect():
    ch = {"finish_reason": "content_filter", "message": {"content": ""}}
    assert completion_looks_like_silent_refusal(ch, "")
    assert not completion_looks_like_silent_refusal(ch, "hello")


def test_silent_refusal_fallback_model():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("OPENROUTER_SILENT_REFUSAL_FALLBACK_MODEL", "openrouter/free")
        assert silent_refusal_fallback_model("deepseek/deepseek-v4-pro") == "openrouter/free"


def test_observation_mask_disabled():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("BRAIN_CONTEXT_OBSERVATION_MASK_ENABLED", "false")
        assert observation_mask_enabled() is False
        rows = [{"role": "assistant", "content": '{"ok": true}' + "z" * 4000}]
        out = mask_observation_dialogue(rows)
        assert out[0]["content"].startswith('{"ok"')
