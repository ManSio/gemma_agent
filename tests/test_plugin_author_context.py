import os
import unittest

from core.brain import plugin_author_context as pac


class PluginAuthorContextTests(unittest.TestCase):
    def tearDown(self) -> None:
        pac.invalidate_plugin_author_handbook_cache()
        os.environ.pop("BRAIN_PLUGIN_AUTHOR_DOCS", None)
        os.environ.pop("BRAIN_PLUGIN_AUTHOR_DOCS_MAX_CHARS", None)

    def test_handbook_present_when_enabled(self):
        os.environ["BRAIN_PLUGIN_AUTHOR_DOCS"] = "true"
        pac.invalidate_plugin_author_handbook_cache()
        text = pac.plugin_author_handbook_for_prompt()
        self.assertIn("Учебник автора плагинов", text)
        self.assertIn("PLUGIN_AUTHOR_HANDBOOK_RU.md", text)

    def test_empty_when_disabled(self):
        os.environ["BRAIN_PLUGIN_AUTHOR_DOCS"] = "false"
        pac.invalidate_plugin_author_handbook_cache()
        self.assertEqual(pac.plugin_author_handbook_for_prompt(), "")


if __name__ == "__main__":
    unittest.main()
