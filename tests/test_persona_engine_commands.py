"""Команды и отображение persona_engine."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from modules.persona_engine import module as pe_mod
from tests.fixtures.telegram_test_ids import TEST_ADMIN_UID


@pytest.fixture()
def persona_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> pe_mod.PersonaEngineModule:
    monkeypatch.setenv("USER_PERSONAS_PATH", str(tmp_path / "user_personas.json"))
    monkeypatch.setenv("GEMMA_PROJECT_ROOT", str(tmp_path))
    return pe_mod.PersonaEngineModule()


def test_get_persona_default_shows_real_user_id(persona_module: pe_mod.PersonaEngineModule) -> None:
    p = persona_module.get_persona(TEST_ADMIN_UID)
    assert p["user_id"] == TEST_ADMIN_UID
    assert p["persona"] == "neutral_mode"
    assert p.get("description")


def test_get_persona_slash_without_args_uses_context_user(persona_module: pe_mod.PersonaEngineModule) -> None:
    out = asyncio.run(
        persona_module.execute(
            {
                "input": {"payload": "/get_persona"},
                "context": {"user_id": "42"},
            }
        )
    )
    assert len(out) == 1
    assert "42" in (out[0].payload or "")
    assert "Пользователь: default" not in (out[0].payload or "")


def test_personas_lists_all_keys(persona_module: pe_mod.PersonaEngineModule) -> None:
    out = asyncio.run(
        persona_module.execute({"input": {"payload": "/personas"}, "context": {"user_id": "1"}})
    )
    assert len(out) == 1
    body = out[0].payload or ""
    assert "neutral_mode" in body
    assert "friend_mode" in body
    assert "/set_persona" in body


def test_set_persona_one_arg_sets_for_actor(persona_module: pe_mod.PersonaEngineModule) -> None:
    uid = "999"
    out = asyncio.run(
        persona_module.execute(
            {"input": {"payload": "/set_persona friend_mode"}, "context": {"user_id": uid}}
        )
    )
    assert len(out) == 1
    assert "✅" in (out[0].payload or "")
    data = json.loads(Path(persona_module.user_personas_file).read_text(encoding="utf-8"))
    assert data[uid]["persona"] == "friend_mode"
