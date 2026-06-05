import os
import unittest
from unittest.mock import patch

from core.brain.agent import format_tools_full_index_for_prompt


class BrainToolIndexTests(unittest.TestCase):
    def test_full_mode_returns_empty(self):
        self.assertEqual(
            format_tools_full_index_for_prompt(
                {"A.x": "a", "B.y": "b"},
                {"A.x": "a"},
                "full",
            ),
            "",
        )

    def test_lite_includes_names_and_counts(self):
        with patch.dict(os.environ, {"BRAIN_TOOLS_FULL_INDEX_IN_PROMPT": "true"}, clear=False):
            s = format_tools_full_index_for_prompt(
                {"Z.z": "z", "A.a": "a"},
                {"A.a": "a"},
                "auto",
            )
        self.assertIn("2", s)
        self.assertIn("auto", s)
        self.assertIn("A.a", s)
        self.assertIn("Z.z", s)

    def test_disabled_by_env(self):
        with patch.dict(os.environ, {"BRAIN_TOOLS_FULL_INDEX_IN_PROMPT": "false"}, clear=False):
            s = format_tools_full_index_for_prompt(
                {"A.x": "a"},
                {},
                "lite",
            )
        self.assertEqual(s, "")


if __name__ == "__main__":
    unittest.main()
