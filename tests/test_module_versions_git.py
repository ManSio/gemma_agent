"""Тесты хелперов scripts/module_versions_git.py (загрузка через importlib)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_script():
    path = ROOT / "scripts" / "module_versions_git.py"
    spec = importlib.util.spec_from_file_location("module_versions_git", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mv():
    return _load_script()


def test_bump_semver(mv):
    assert mv.bump_semver("1.2.3", "patch") == "1.2.4"
    assert mv.bump_semver("0.0.9", "minor") == "0.1.0"
    assert mv.bump_semver("2.1.0", "major") == "3.0.0"


def test_bump_semver_suffix_preserved(mv):
    assert mv.bump_semver("1.0.0-beta", "patch") == "1.0.1-beta"


def test_manifest_needs_version_bump(mv):
    a = {"name": "x", "version": "1.0.0", "bundled_with": "2.0.0"}
    b = dict(a)
    assert not mv.manifest_needs_version_bump(a, b)
    b2 = dict(a, bundled_with="3.0.0")
    assert not mv.manifest_needs_version_bump(a, b2)
    b3 = dict(a, description="y")
    assert mv.manifest_needs_version_bump(a, b3)
    assert mv.manifest_needs_version_bump(None, a)
