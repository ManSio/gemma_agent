from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import tempfile
from pathlib import Path

from core.self_programming import SelfProgrammingModule, _infer_domain_template


def test_generate_module_code_is_not_stub() -> None:
    sp = SelfProgrammingModule()
    src = sp._generate_module_code(  # noqa: SLF001
        "demo_plugin",
        "Demo plugin",
        command_triggers=["/demo_run"],
    )
    assert "safe_eval_arithmetic" in src
    assert "action == \"calc\"" in src
    assert "action == \"stats\"" in src
    assert "TRIGGERS = ['/demo_run']" in src


def test_generate_module_writes_rich_template() -> None:
    with tempfile.TemporaryDirectory() as td:
        mods = Path(td) / "modules"
        mods.mkdir(parents=True, exist_ok=True)
        sp = SelfProgrammingModule(modules_path=str(mods))
        out = asyncio.run(
            sp.generate_module(
                module_name="user_requested_plugin_x",
                description="Demo plugin",
                commands=[{"trigger": "/ux_run", "description": "run"}],
            )
        )
        assert out.get("success") is True
        assert (out.get("strict_report") or {}).get("ok") is True
        code = (mods / "user_requested_plugin_x" / "module.py").read_text(encoding="utf-8")
        assert "safe_eval_arithmetic" in code
        assert "Действия:" in code


def test_infer_domain_template() -> None:
    assert _infer_domain_template("todo manager for tasks", "x") == "todo"
    assert _infer_domain_template("weather simulation", "x") == "weather"
    assert _infer_domain_template("regex parser helper", "x") == "parser"
    assert _infer_domain_template("health monitor", "x") == "monitoring"


def test_generate_module_writes_domain_template_code() -> None:
    with tempfile.TemporaryDirectory() as td:
        mods = Path(td) / "modules"
        mods.mkdir(parents=True, exist_ok=True)
        sp = SelfProgrammingModule(modules_path=str(mods))
        out = asyncio.run(
            sp.generate_module(
                module_name="user_requested_plugin_tasks",
                description="Task and todo manager plugin",
                commands=[{"trigger": "/tasks_run", "description": "run"}],
            )
        )
        assert out.get("success") is True
        code = (mods / "user_requested_plugin_tasks" / "module.py").read_text(encoding="utf-8")
        assert "DOMAIN_TEMPLATE = 'todo'" in code
        assert 'action == "todo_add"' in code
        assert 'action == "todo_list"' in code
        assert "COMMAND_TO_ACTION" in code

        manifest = json.loads((mods / "user_requested_plugin_tasks" / "module.json").read_text(encoding="utf-8"))
        triggers = [str((c or {}).get("trigger") or "") for c in manifest.get("commands") or []]
        assert any("todo_add" in t for t in triggers)
        assert any("todo_list" in t for t in triggers)
        buttons = manifest.get("buttons") or []
        assert len(buttons) >= 1


def test_generated_todo_domain_commands_execute() -> None:
    with tempfile.TemporaryDirectory() as td:
        mods = Path(td) / "modules"
        mods.mkdir(parents=True, exist_ok=True)
        sp = SelfProgrammingModule(modules_path=str(mods))
        out = asyncio.run(
            sp.generate_module(
                module_name="user_requested_plugin_tasks_rt",
                description="Task and todo manager plugin",
                commands=[{"trigger": "/tasksrt_todo_add", "description": "add"}],
            )
        )
        assert out.get("success") is True

        module_file = mods / "user_requested_plugin_tasks_rt" / "module.py"
        spec = importlib.util.spec_from_file_location("tmp_gen_mod", module_file)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cls = getattr(mod, "UserRequestedPluginTasksRtModule")
        inst = cls()

        r1 = asyncio.run(
            inst.execute({"input": {"payload": "/tasksrt_todo_add купить молоко"}, "context": {"user_id": "u1"}})
        )
        assert isinstance(r1, list) and r1
        assert "Добавлено задач" in r1[0].payload

        r2 = asyncio.run(
            inst.execute({"input": {"payload": "/tasksrt_todo_list"}, "context": {"user_id": "u1"}})
        )
        assert isinstance(r2, list) and r2
        assert "купить молоко" in r2[0].payload


def test_strict_mode_rolls_back_on_validation_failure() -> None:
    with tempfile.TemporaryDirectory() as td:
        mods = Path(td) / "modules"
        mods.mkdir(parents=True, exist_ok=True)
        sp = SelfProgrammingModule(modules_path=str(mods))
        orig = sp._generate_tests  # noqa: SLF001

        def _bad_tests(_name: str) -> str:
            return "import unittest\n"

        sp._generate_tests = _bad_tests  # type: ignore[attr-defined]
        os.environ["SELF_PROGRAMMING_STRICT_MODE"] = "1"
        os.environ["SELF_PROGRAMMING_STRICT_ROLLBACK"] = "1"
        try:
            out = asyncio.run(
                sp.generate_module(
                    module_name="user_requested_plugin_bad",
                    description="Task and todo manager plugin",
                    commands=[{"trigger": "/bad_todo_add", "description": "add"}],
                )
            )
            assert out.get("success") is False
            sr = out.get("strict_report") or {}
            assert sr.get("ok") is False
            assert "strict gate failed" in str(sr.get("error") or "")
            assert not (mods / "user_requested_plugin_bad").exists()
        finally:
            sp._generate_tests = orig  # type: ignore[attr-defined]
            os.environ.pop("SELF_PROGRAMMING_STRICT_MODE", None)
            os.environ.pop("SELF_PROGRAMMING_STRICT_ROLLBACK", None)
