"""Profile engine: registry (27), router, classifier, tools."""
import pytest

from core.brain.constants import BRAIN_PROFILES
from core.brain.profile_registry import (
    all_profile_names,
    is_valid_profile,
    merge_classifier_profile,
    normalize_profile,
    profile_for_intent,
    profile_from_text_heuristics,
    refine_profile,
    resolve_tool_prefixes,
)
from core.brain.router_classifier import _ALL_PROFILES
from core.brain.agent import tools_for_profile


def test_registry_has_28_profiles():
    names = all_profile_names()
    assert len(names) == 29
    assert len(BRAIN_PROFILES) == 29
    assert set(BRAIN_PROFILES) == set(names)


def test_router_knows_all_profiles():
    assert len(_ALL_PROFILES) == 29
    assert _ALL_PROFILES == frozenset(all_profile_names())


def test_normalize_invalid_profile():
    assert normalize_profile("bogus") == "standard"
    assert normalize_profile("legal") == "legal"
    assert normalize_profile("перевод") == "translation"


def test_refine_profile_translation():
    p = refine_profile("standard", "переведи на английский hello", "general", confidence=0.9)
    assert p == "translation"


def test_merge_classifier_overrides_generic():
    base = merge_classifier_profile(
        "standard",
        {"profile": "legal", "need_memory": "true", "need_verify": "false"},
        router_confidence=0.5,
    )
    assert base == "legal"


def test_tools_translation_empty():
    tools = tools_for_profile("translation", {"LawSearch.search": "x", "UniversalSearch.search": "y"}, "")
    assert tools == {}


def test_tools_legal_subset():
    full = {
        "LawSearch.search": "a",
        "UniversalSearch.search": "b",
        "DocumentCorpus.unified_search": "d",
        "SelfProgramming.generate_module": "c",
    }
    out = tools_for_profile("legal", full, "")
    assert "UniversalSearch.search" in out
    assert "DocumentCorpus.unified_search" in out
    assert "LawSearch.search" not in out
    assert "SelfProgramming.generate_module" not in out


def test_intent_maps_code_review():
    assert profile_for_intent("code_review") == "code_review"


def test_classifier_sanitize(monkeypatch):
    from core.brain.classifier import _sanitize_classifier_result

    bad = _sanitize_classifier_result({"profile": "not_a_profile", "need_memory": "false", "need_verify": "false"})
    assert bad is not None and bad["profile"] == "standard"
    ok = _sanitize_classifier_result({"profile": "math_solve", "need_memory": "false", "need_verify": "false"})
    assert ok and ok["profile"] == "math_solve"


def test_resolve_tool_prefixes_deep_all():
    assert resolve_tool_prefixes("deep") is None


def test_resolve_tool_prefixes_short_empty():
    assert resolve_tool_prefixes("short") == set()
