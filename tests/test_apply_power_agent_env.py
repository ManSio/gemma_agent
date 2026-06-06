"""Smoke test for POWER_AGENT env applier."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "apply_power_agent_env.py"
    spec = importlib.util.spec_from_file_location("apply_power_agent_env", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_apply_power_agent_patches_env(tmp_path):
    mod = _load_module()
    env = tmp_path / ".env"
    env.write_text(
        "TELEGRAM_TOKEN=test\nGOAL_RUNNER_ENABLED=false\nSELF_VERIFY_ACTIVE=false\n",
        encoding="utf-8",
    )
    keys = mod._patch_env(env)
    text = env.read_text(encoding="utf-8")
    assert "GOAL_RUNNER_ENABLED=true" in text
    assert "SELF_VERIFY_ACTIVE=true" in text
    assert "TURN_QUALITY_LOOP_ENABLED=true" in text
    assert "MCE_ENABLED=false" in text
    assert len(keys) >= 10
