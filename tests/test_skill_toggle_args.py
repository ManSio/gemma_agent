import os
from pathlib import Path

from modules.skills.registry import SkillRegistry, parse_skill_toggle_args
from modules.skills.skill_interface import Skill


class _Dummy(Skill):
    name = "translator"

    async def run(self, *args, **kwargs):
        return ""


def test_parse_skill_toggle_args():
    assert parse_skill_toggle_args("translator") == ("translator", None)
    assert parse_skill_toggle_args("translator enabled") == ("translator", True)
    assert parse_skill_toggle_args("translator off") == ("translator", False)
    assert parse_skill_toggle_args("translator выкл") == ("translator", False)
    assert parse_skill_toggle_args("my_skill on") == ("my_skill", True)


def test_set_enabled_not_toggle_whole_phrase():
    reg = SkillRegistry()
    reg.register(_Dummy())
    reg.set_enabled("translator", False)
    assert reg.get("translator") is None
    reg.set_enabled("translator", True)
    assert reg.get("translator") is not None


def test_enabled_state_persists(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GEMMA_PROJECT_ROOT", str(tmp_path))
    reg1 = SkillRegistry()
    reg1.register(_Dummy())
    reg1.set_enabled("translator", False)
    reg2 = SkillRegistry()
    reg2.register(_Dummy())
    reg2.load_persisted_enabled()
    assert reg2.get("translator") is None
