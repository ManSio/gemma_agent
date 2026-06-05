import json
from pathlib import Path

import pytest

from core import model_profile as mp


def test_resolve_gemini_builtin():
    p = mp.resolve_model_profile("google/gemini-2.0-flash-exp:free")
    assert p.match_label == "gemini"
    assert p.reasoning_scaffold == "short"
    assert p.system_addon_first


def test_resolve_default_unknown():
    p = mp.resolve_model_profile("some-vendor/unknown-model-x")
    assert p.match_label == "default"


def test_resolve_deepseek_r1_before_deepseek():
    p = mp.resolve_model_profile("deepseek/deepseek-r1-something")
    assert p.match_label == "deepseek-r1"


def test_json_overlay_priority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = tmp_path / "profiles.json"
    cfg.write_text(
        json.dumps(
            [
                {
                    "match": "acme/",
                    "match_label": "acme_custom",
                    "system_addon_first": "ACME_RULE",
                    "reasoning_scaffold": "omit",
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MODEL_PROFILES_PATH", str(cfg))
    mp._OVERLAY_MTIME = None
    mp._OVERLAY_PAIRS = []
    p = mp.resolve_model_profile("acme/test-model")
    assert p.match_label == "acme_custom"
    assert "ACME_RULE" in p.system_addon_first
    assert p.reasoning_scaffold == "omit"


def test_clamp_temperature():
    assert mp.clamp_temperature(0.4, 0.2) == pytest.approx(0.6)
    assert mp.clamp_temperature(0.4, 0.9) == 0.95
    assert mp.clamp_temperature(0.4, -0.5) == pytest.approx(0.05)
