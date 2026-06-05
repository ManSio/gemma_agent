import tempfile
import unittest
from pathlib import Path

from core.plugin_admin_ops import is_generated_plugin_name, normalize_plugin_name, safe_plugin_dir


class PluginAdminOpsTests(unittest.TestCase):
    def test_normalize_plugin_name(self):
        self.assertEqual(normalize_plugin_name(" /user_requested_plugin_6 "), "user_requested_plugin_6")
        self.assertEqual(normalize_plugin_name("modules/user_requested_plugin_2"), "user_requested_plugin_2")

    def test_generated_name_detection(self):
        self.assertTrue(is_generated_plugin_name("user_requested_plugin_9"))
        self.assertFalse(is_generated_plugin_name("math"))

    def test_safe_plugin_dir_normalizes_to_modules_root(self):
        root = Path(tempfile.gettempdir()) / "gemma_bot_test_modules"
        p = safe_plugin_dir(root, "../oops")
        self.assertIsNotNone(p)
        assert p is not None
        self.assertEqual(p, (root.resolve() / "oops").resolve())


if __name__ == "__main__":
    unittest.main()
