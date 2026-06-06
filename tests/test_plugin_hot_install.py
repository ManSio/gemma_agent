import json
from pathlib import Path

from core.plugin_registry import PluginRegistry


def _ensure_pkg(base: Path) -> None:
    base.mkdir(parents=True, exist_ok=True)
    init = base / "__init__.py"
    if not init.exists():
        init.write_text("", encoding="utf-8")


def _write_min_plugin(root_pkg: str, base: Path, name: str) -> None:
    _ensure_pkg(base)
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "__init__.py").write_text("", encoding="utf-8")
    cls = name.capitalize() + "Module"
    manifest = {
        "name": name,
        "version": "1.0.0",
        "type": "tool",
        "description": "test",
        "entrypoint": f"{root_pkg}.{name}.module:{cls}",
        "input_types": ["text"],
        "output_types": ["text"],
        "commands": [],
        "buttons": [],
        "config_schema": {"type": "object", "properties": {}, "required": []},
        "requires": [],
        "pip_requirements": [],
    }
    (d / "module.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    code = f'''
from typing import Any, Dict, List
from core.models import Output

class {cls}:
    async def execute(self, args: Dict[str, Any]) -> List[Output]:
        return [Output(type="text", payload="ok", meta={{"module": "{name}"}})]
'''
    (d / "module.py").write_text(code, encoding="utf-8")


def test_hot_install_loads_new_module(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    pkg = "testmods_ci"
    mods = tmp_path / pkg
    name = "hotplug_ci"
    _write_min_plugin(pkg, mods, name)

    reg = PluginRegistry(str(mods))
    assert name not in reg.loaded_modules
    out = reg.hot_install_module(name)
    assert out.get("success") is True
    probe = out.get("probe")
    assert isinstance(probe, dict) and probe.get("ok") is True
    assert name in reg.loaded_modules
    inst = reg.loaded_modules[name].instance
    import asyncio

    res = asyncio.run(inst.execute({"input": {"payload": "x"}}))
    assert res[0].payload == "ok"


def test_hot_install_reload_picks_up_code_change(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    pkg = "testmods_rl"
    mods = tmp_path / pkg
    name = "hotplug_reload"
    _write_min_plugin(pkg, mods, name)
    reg = PluginRegistry(str(mods))
    reg.hot_install_module(name)

    cls = name.capitalize() + "Module"
    new_code = f'''
from typing import Any, Dict, List
from core.models import Output

class {cls}:
    async def execute(self, args: Dict[str, Any]) -> List[Output]:
        return [Output(type="text", payload="v2", meta={{"module": "{name}"}})]
'''
    (mods / name / "module.py").write_text(new_code, encoding="utf-8")
    out = reg.hot_install_module(name)
    assert out.get("success") is True
    inst = reg.loaded_modules[name].instance
    import asyncio

    res = asyncio.run(inst.execute({"input": {"payload": "x"}}))
    assert res[0].payload == "v2"
