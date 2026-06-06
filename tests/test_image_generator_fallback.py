"""Диагностика пустого image-ответа OpenRouter и подпись при fallback."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

from modules.image_generator.module import (
    ImageGeneratorModule,
    diagnose_chat_response,
    format_api_fallback_notice,
    format_fallback_notice,
    image_finish_label,
    short_model_label,
)

_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_diagnose_image_other_finish():
    data = {
        "native_finish_reason": "IMAGE_OTHER",
        "service_tier": "default",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "", "images": []},
            }
        ],
    }
    diag = diagnose_chat_response(data)
    assert diag["native_finish_reason"] == "IMAGE_OTHER"
    assert diag["service_tier"] == "default"
    assert diag["finish_reason"] == "stop"
    assert image_finish_label(diag) == "IMAGE_OTHER"


def test_diagnose_message_excerpt():
    data = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Не могу обработать этот запрос."}],
                },
            }
        ],
    }
    diag = diagnose_chat_response(data)
    assert "Не могу" in diag.get("message_excerpt", "")


def test_format_fallback_notice():
    text = format_fallback_notice(
        primary_model="google/gemini-3.1-flash-image-preview",
        fallback_model="google/gemini-2.5-flash-image",
        reason="IMAGE_OTHER",
        message_excerpt="policy",
    )
    assert "gemini-3.1-flash-image-preview" in text
    assert "gemini-2.5-flash-image" in text
    assert "IMAGE_OTHER" in text
    assert "policy" in text


def test_short_model_label():
    assert short_model_label("google/gemini-2.5-flash-image") == "gemini-2.5-flash-image"


def _ok_image_response() -> Dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "images": [
                        {
                            "image_url": {
                                "url": f"data:image/png;base64,{_TINY_PNG_B64}",
                            }
                        }
                    ],
                },
            }
        ],
    }


def _empty_image_response(*, native: str = "IMAGE_OTHER") -> Dict[str, Any]:
    return {
        "native_finish_reason": native,
        "choices": [{"finish_reason": "stop", "message": {"content": "", "images": []}}],
    }


def test_execute_fallback_notice_on_empty_primary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("IMAGE_GEN_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("IMAGE_GEN_MODEL", "google/gemini-3.1-flash-image-preview")
    monkeypatch.setenv("IMAGE_GEN_MODEL_FALLBACK", "google/gemini-2.5-flash-image")
    monkeypatch.setenv("IMAGE_GEN_FALLBACK_USER_NOTICE", "true")

    mod = ImageGeneratorModule()
    calls: List[str] = []

    async def fake_call(*, prompt: str, model: str, reference_paths=None) -> Dict[str, Any]:
        calls.append(model)
        if model == mod.model:
            return {"ok": True, "data": _empty_image_response()}
        return {"ok": True, "data": _ok_image_response()}

    monkeypatch.setattr(mod, "_call_openrouter", AsyncMock(side_effect=fake_call))

    out = asyncio.run(
        mod.execute({"input": {"payload": "сделай с фото как в фильме", "meta": {"user_id": "1"}}})
    )
    assert len(out) == 1
    assert "IMAGE_OTHER" in out[0].payload or "не вернула изображение" in out[0].payload
    assert "gemini-2.5-flash-image" in out[0].payload
    assert out[0].meta.get("image_output_path")
    assert calls[0] == mod.model
    assert calls[-1] == mod.fallback_model


def test_execute_api_fail_then_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("IMAGE_GEN_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setenv("IMAGE_GEN_MODEL", "google/gemini-3.1-flash-image-preview")
    monkeypatch.setenv("IMAGE_GEN_MODEL_FALLBACK", "google/gemini-2.5-flash-image")

    mod = ImageGeneratorModule()

    async def fake_call(*, prompt: str, model: str, reference_paths=None) -> Dict[str, Any]:
        if model == mod.model:
            return {"ok": False, "error": "http_429: rate limit"}
        return {"ok": True, "data": _ok_image_response()}

    monkeypatch.setattr(mod, "_call_openrouter", AsyncMock(side_effect=fake_call))

    out = asyncio.run(mod.execute({"input": {"payload": "нарисуй кота", "meta": {"user_id": "2"}}}))
    notice = format_api_fallback_notice(
        primary_model=mod.model,
        fallback_model=mod.fallback_model,
        api_error="http_429: rate limit",
    )
    assert notice.split(".")[0] in out[0].payload
    assert out[0].meta.get("image_output_path")
