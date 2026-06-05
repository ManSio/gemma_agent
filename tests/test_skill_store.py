"""Tests for core/skill_store.py — Skill crystallization."""

import json
import os
import tempfile

from core.skill_store import (
    SkillStoreModule,
    _load_skills,
    _sanitize_name,
    _auto_count_reset,
    auto_crystallize,
)


def _with_temp_skills(test_fn):
    import functools

    @functools.wraps(test_fn)
    def wrapper(*args, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "skills.json")
            os.environ["GEMMA_SKILLS_PATH"] = path
            try:
                return test_fn(*args, **kwargs)
            finally:
                os.environ.pop("GEMMA_SKILLS_PATH", None)
    return wrapper


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


# ── _sanitize_name ──


def test_sanitize_replaces_spaces():
    assert _sanitize_name("my skill") == "my_skill"


def test_sanitize_lowercases():
    assert _sanitize_name("HELLO") == "hello"


def test_sanitize_removes_special_chars():
    assert _sanitize_name("hello!!! world???") == "hello_world"


def test_sanitize_keeps_hyphen_underscore():
    assert _sanitize_name("my-skill_v2") == "my-skill_v2"


# ── Save ──


@_with_temp_skills
def test_skill_save_creates():
    mod = SkillStoreModule()
    r = asyncio_run(mod.skill_save(name="test_skill", description="a test", steps="Step 1: do X\nStep 2: do Y\nStep 3: profit" * 4))
    assert r["ok"] is True
    assert r["action"] == "created"
    assert r["skill"] == "test_skill"


@_with_temp_skills
def test_skill_save_updates():
    mod = SkillStoreModule()
    asyncio_run(mod.skill_save(name="sk1", description="x", steps="A " * 25 + "B " * 25))
    r = asyncio_run(mod.skill_save(name="sk1", description="updated", steps="X " * 25 + "Y " * 25))
    assert r["action"] == "updated"


@_with_temp_skills
def test_skill_save_validates_name():
    mod = SkillStoreModule()
    r = asyncio_run(mod.skill_save(name="ab", steps="x " * 50))
    assert r["ok"] is False
    assert "name must be" in r["error"]


@_with_temp_skills
def test_skill_save_validates_steps():
    mod = SkillStoreModule()
    r = asyncio_run(mod.skill_save(name="valid_name", steps="too short"))
    assert r["ok"] is False
    assert "steps must be" in r["error"]


@_with_temp_skills
def test_skill_save_sanitizes_spaces():
    mod = SkillStoreModule()
    r = asyncio_run(mod.skill_save(name="my weather skill", description="test", steps="S " * 20))
    assert r["ok"] is True
    assert r["skill"] == "my_weather_skill"


@_with_temp_skills
def test_skill_save_min_steps_20():
    mod = SkillStoreModule()
    r = asyncio_run(mod.skill_save(name="short_skill", steps="x" * 20))
    assert r["ok"] is True


# ── List ──


@_with_temp_skills
def test_skill_list_empty():
    mod = SkillStoreModule()
    r = asyncio_run(mod.skill_list())
    assert r["ok"] is True
    assert r["count"] == 0
    assert r["skills"] == []


@_with_temp_skills
def test_skill_list_with_items():
    mod = SkillStoreModule()
    asyncio_run(mod.skill_save(name="skill_a", description="d1", steps="S " * 30, category="cat_a"))
    asyncio_run(mod.skill_save(name="skill_b", description="d2", steps="T " * 30, category="cat_b"))
    r = asyncio_run(mod.skill_list())
    assert r["count"] == 2
    names = {s["name"] for s in r["skills"]}
    assert names == {"skill_a", "skill_b"}


@_with_temp_skills
def test_skill_list_filter_by_category():
    mod = SkillStoreModule()
    asyncio_run(mod.skill_save(name="skill_a", description="d1", steps="S " * 30, category="cat_a"))
    asyncio_run(mod.skill_save(name="skill_b", description="d2", steps="T " * 30, category="cat_b"))
    r = asyncio_run(mod.skill_list(category="cat_a"))
    assert r["count"] == 1
    assert r["skills"][0]["name"] == "skill_a"


# ── Get ──


@_with_temp_skills
def test_skill_get_found():
    mod = SkillStoreModule()
    asyncio_run(mod.skill_save(name="my_skill", description="desc", steps="S " * 30))
    r = asyncio_run(mod.skill_get(name="my_skill"))
    assert r["ok"] is True
    assert r["skill"]["name"] == "my_skill"
    assert "S " in r["skill"]["steps"]


@_with_temp_skills
def test_skill_get_not_found():
    mod = SkillStoreModule()
    r = asyncio_run(mod.skill_get(name="nonexistent"))
    assert r["ok"] is False
    assert "not found" in r["error"]


# ── Run ──


@_with_temp_skills
def test_skill_run_returns_steps():
    mod = SkillStoreModule()
    asyncio_run(mod.skill_save(name="greet", steps="S " * 20))
    r = asyncio_run(mod.skill_run(name="greet"))
    assert r["ok"] is True
    assert "steps" in r
    assert r["times_used"] == 1  # incremented from 0


@_with_temp_skills
def test_skill_run_increments_usage():
    mod = SkillStoreModule()
    asyncio_run(mod.skill_save(name="counter_test", steps="S " * 20))
    asyncio_run(mod.skill_run(name="counter_test"))
    asyncio_run(mod.skill_run(name="counter_test"))
    r = asyncio_run(mod.skill_get(name="counter_test"))
    assert r["skill"]["times_used"] == 2


@_with_temp_skills
def test_skill_run_not_found():
    mod = SkillStoreModule()
    r = asyncio_run(mod.skill_run(name="no_such_skill"))
    assert r["ok"] is False


# ── Delete ──


@_with_temp_skills
def test_skill_delete():
    mod = SkillStoreModule()
    asyncio_run(mod.skill_save(name="del_me", description="x", steps="S " * 30))
    r = asyncio_run(mod.skill_delete(name="del_me"))
    assert r["ok"] is True
    assert r["action"] == "deleted"
    r2 = asyncio_run(mod.skill_list())
    assert r2["count"] == 0


@_with_temp_skills
def test_skill_delete_not_found():
    mod = SkillStoreModule()
    r = asyncio_run(mod.skill_delete(name="no_such"))
    assert r["ok"] is False


# ── Persistence ──


@_with_temp_skills
def test_skills_persist_to_disk():
    mod = SkillStoreModule()
    asyncio_run(mod.skill_save(name="persist_test", description="check", steps="S " * 30))
    loaded = _load_skills()
    assert "persist_test" in loaded
    assert loaded["persist_test"]["description"] == "check"


# ── Module auto-discoverable ──


def test_module_is_auto_discoverable():
    from core.skill_store import SkillStoreModule as Cls
    assert Cls.__name__.endswith("Module")
    assert Cls.BRAIN_LITE_INCLUDE is True
    instance = Cls()
    for name in ("skill_save", "skill_list", "skill_get", "skill_delete", "skill_run"):
        assert hasattr(instance, name), f"missing {name}"
        assert callable(getattr(instance, name)), f"{name} not callable"


# ── Auto-crystallize ──


@_with_temp_skills
def test_auto_crystallize_below_threshold():
    _auto_count_reset("fp_test_1")
    result = auto_crystallize(fp="fp_test_1", intent="test", module="m", steps_summary="step 1", assistant_excerpt="ok")
    assert result is None


@_with_temp_skills
def test_auto_crystallize_at_threshold():
    _auto_count_reset("fp_test_2")
    # Call 3 times to reach threshold
    assert auto_crystallize(fp="fp_test_2", intent="test", module="m", steps_summary="step 1", assistant_excerpt="ok") is None
    assert auto_crystallize(fp="fp_test_2", intent="test", module="m", steps_summary="step 1", assistant_excerpt="ok") is None
    result = auto_crystallize(fp="fp_test_2", intent="test", module="m", steps_summary="step 1", assistant_excerpt="ok")
    assert result is not None
    assert "auto_" in result
    # Verify it was saved
    loaded = _load_skills()
    assert result in loaded


@_with_temp_skills
def test_auto_crystallize_resets_after_threshold():
    _auto_count_reset("fp_test_3")
    auto_crystallize(fp="fp_test_3", intent="t", module="m", steps_summary="s", assistant_excerpt="ok")
    auto_crystallize(fp="fp_test_3", intent="t", module="m", steps_summary="s", assistant_excerpt="ok")
    auto_crystallize(fp="fp_test_3", intent="t", module="m", steps_summary="s", assistant_excerpt="ok")
    auto_crystallize(fp="fp_test_3", intent="t", module="m", steps_summary="s", assistant_excerpt="ok")  # 4th call
    loaded = _load_skills()
    auto_skills = {k: v for k, v in loaded.items() if k.startswith("auto_")}
    assert len(auto_skills) == 1  # not duplicated
