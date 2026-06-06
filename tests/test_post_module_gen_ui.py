import unittest

from core.brain import post_module_gen_ui as pmg


class PostModuleGenUiTests(unittest.TestCase):
    def test_first_slash_from_commands(self):
        self.assertEqual(
            pmg.first_slash_from_commands([{"name": "/foo", "description": "x"}]),
            "/foo",
        )
        self.assertEqual(
            pmg.first_slash_from_commands([{"trigger": "bar", "description": "x"}]),
            "/bar",
        )

    def test_build_rows(self):
        rows = pmg.build_post_module_gen_keyboard_rows(
            {
                "module_name": "my_mod",
                "commands": [{"trigger": "/my_mod_run", "description": "go"}],
            }
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), 2)
        self.assertTrue(rows[0][0]["callback_data"].startswith("pgen:t:my_mod"))


if __name__ == "__main__":
    unittest.main()
