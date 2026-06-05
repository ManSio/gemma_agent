"""Регрессия для scripts/mutation_guard_l2.py (без запуска mutmut)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = ROOT / "scripts" / "mutation_guard_l2.py"


def _load_guard():
    spec = importlib.util.spec_from_file_location("mutation_guard_l2", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_path_to_mutant_pattern():
    g = _load_guard()
    assert g._path_to_mutant_pattern("core/brain/profile_route_guard.py") == (
        "core.brain.profile_route_guard*"
    )


def test_parse_score_survived_ratio():
    g = _load_guard()
    text = "12 / 40 mutants survived"
    assert g._parse_score(text) == 70.0


def test_parse_score_survived_paren_count():
    g = _load_guard()
    text = "Survived 🙁 (231)\n\nKilled 🎉 (142)"
    assert g._parse_score(text) == pytest.approx(100.0 * 142 / 373, rel=0.01)


def test_parse_score_mutmut_progress_emoji_line():
    g = _load_guard()
    text = "⠇ 273/273  🎉 76  ⏰ 0  🤔 0  🙁 197  🔇 0\n"
    assert g._parse_score(text) == pytest.approx(100.0 * 76 / 273, rel=0.01)


def test_clear_mutants_cache_removes_dot_cache(tmp_path, monkeypatch):
    g = _load_guard()
    cache = tmp_path / ".mutmut-cache"
    cache.write_bytes(b"stale")
    mutants = tmp_path / "mutants"
    mutants.mkdir()
    (mutants / "x").write_text("1")
    monkeypatch.setattr(g, "ROOT", tmp_path)
    monkeypatch.setattr(g, "_MUTMUT_CACHE", cache)
    monkeypatch.setattr(g, "_MUTANTS_DIR", mutants)
    g._clear_mutants_cache()
    assert not cache.exists()
    assert not mutants.exists()


def test_parse_score_percent():
    g = _load_guard()
    assert g._parse_score("Mutation score: 82.5%") == 82.5


def test_replace_mutmut_section():
    g = _load_guard()
    original = "[mutmut]\nold=yes\n\n[pytest]\nfoo=1\n"
    new = "[mutmut]\nnew=yes\n"
    out = g._replace_mutmut_section(original, new)
    assert "[mutmut]\nnew=yes" in out
    assert "old=yes" not in out
    assert "[pytest]\nfoo=1" in out


def test_guard_source_uses_paths_to_mutate_for_v2():
    text = _SCRIPT.read_text(encoding="utf-8")
    assert 'f"--paths-to-mutate={' in text or "f'--paths-to-mutate={" in text


def test_mutmut_version_ok_accepts_24_prefix():
    g = _load_guard()
    ok, ver = g._mutmut_version_ok()
    try:
        import mutmut  # noqa: F401
    except ImportError:
        return
    if ver.startswith("2.4."):
        assert ok is True
    elif ver and ver != "unknown":
        assert ok is False


def test_setup_cfg_v2_overlay_block():
    g = _load_guard()
    original = g._SETUP_CFG.read_text(encoding="utf-8")
    with g._setup_cfg_overlay_v2("core/brain/profile_route_guard.py", ["tests/test_profile_route_guard.py"]):
        block = g._SETUP_CFG.read_text(encoding="utf-8")
    assert "paths_to_mutate=" in block
    assert "profile_route_guard.py" in block
    assert "test_profile_route_guard.py" in block
    assert g._SETUP_CFG.read_text(encoding="utf-8") == original
