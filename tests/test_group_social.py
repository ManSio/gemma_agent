import unittest

from core.group_social import augment_system_prompt_for_group


class GroupSocialTests(unittest.TestCase):
    def test_off_and_augment(self):
        import os

        os.environ["GROUP_SOCIAL_ENABLED"] = "0"
        base = "Ты помощник."
        self.assertEqual(augment_system_prompt_for_group(base, group_id="-1"), base)
        os.environ["GROUP_SOCIAL_ENABLED"] = "1"
        os.environ["GROUP_SOCIAL_MODE"] = "friend"
        os.environ.pop("GROUP_SOCIAL_PROMPT_ADDON", None)
        out = augment_system_prompt_for_group(base, group_id="-1")
        self.assertIn("групповом", out)
        self.assertTrue(out.startswith("Ты помощник."))
        os.environ.pop("GROUP_SOCIAL_ENABLED", None)
        os.environ.pop("GROUP_SOCIAL_MODE", None)


if __name__ == "__main__":
    unittest.main()
