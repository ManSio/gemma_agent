"""Проверка манифестов и загрузки integration-модулей (раньше skip: no module.json)."""
from __future__ import annotations

import unittest
from pathlib import Path

from core.plugin_registry import PluginRegistry

ROOT = Path(__file__).resolve().parent.parent
MODULES = ROOT / "modules"
SHELL_NAMES = ("external_apis", "imaging", "skills")


class PluginShellModulesTests(unittest.TestCase):
    def test_each_shell_registers_and_enables(self) -> None:
        reg = PluginRegistry(str(MODULES))
        for name in SHELL_NAMES:
            with self.subTest(name=name):
                inst = reg.load_module(MODULES / name)
                self.assertIsNotNone(inst, f"load_module failed for {name}")
                self.assertTrue(reg.enable_module(name), f"enable_module failed for {name}")
                mod = reg.get_module(name)
                self.assertIsNotNone(mod, f"get_module returned None for {name}")
                self.assertEqual(mod.state.status, "healthy")


if __name__ == "__main__":
    unittest.main()
