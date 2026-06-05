"""Контроллер плагинов: denylist и фильтр промптов."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from core.plugin_controller import PluginController


class PluginControllerTests(unittest.TestCase):
    def test_routable_without_denylist(self):
        reg = MagicMock()
        with patch.dict("os.environ", {}, clear=False):
            c = PluginController(reg)
            self.assertTrue(c.is_routable("echo"))
            self.assertTrue(c.is_routable("Books_RAG"))

    def test_denylist_case_insensitive(self):
        reg = MagicMock()
        with patch.dict("os.environ", {"PLUGIN_CONTROLLER_DENYLIST": "foo, BAR"}, clear=False):
            c = PluginController(reg)
            self.assertFalse(c.is_routable("foo"))
            self.assertFalse(c.is_routable("Bar"))
            self.assertTrue(c.is_routable("echo"))

    def test_filter_module_keys(self):
        reg = MagicMock()
        with patch.dict("os.environ", {"PLUGIN_CONTROLLER_DENYLIST": "a"}, clear=False):
            c = PluginController(reg)
            self.assertEqual(c.filter_module_keys({"a", "b", "c"}), {"b", "c"})


if __name__ == "__main__":
    unittest.main()
