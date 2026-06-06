from __future__ import annotations

import json
from pathlib import Path

from core.group_chat_policy import load_group_chat_policy, save_group_chat_policy


def test_group_chat_policy_defaults(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GEMMA_PROJECT_ROOT", str(tmp_path))
    pol = load_group_chat_policy()
    assert pol["active_mode"] is False
    assert pol["participate_mode"] == "mention"
    assert pol["group_memory_max"] == 12


def test_group_chat_policy_save_and_clamp(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GEMMA_PROJECT_ROOT", str(tmp_path))
    pol = save_group_chat_policy({"active_mode": True, "group_memory_max": 999})
    assert pol["active_mode"] is True
    assert pol["group_memory_max"] == 40
    p = tmp_path / "data" / "runtime" / "group_chat_policy.json"
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw["active_mode"] is True
    assert raw["participate_mode"] == "active"
    assert raw["group_memory_max"] == 40


def test_group_chat_policy_balanced_mode(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GEMMA_PROJECT_ROOT", str(tmp_path))
    pol = save_group_chat_policy({"participate_mode": "smart"})
    assert pol["participate_mode"] == "balanced"
    assert pol["active_mode"] is False
