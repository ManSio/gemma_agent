import unittest

from core.prompt_assembly import (
    PromptAssemblyTier,
    brain_prompt_tier,
    snapshot_context_policy,
)


class PromptAssemblyTests(unittest.TestCase):
    def test_tiers_map_to_branch_flags(self):
        self.assertEqual(
            brain_prompt_tier(use_slim_image=True, hot_path_slim=False),
            PromptAssemblyTier.IMAGE_SLIM,
        )
        self.assertEqual(
            brain_prompt_tier(use_slim_image=False, hot_path_slim=True),
            PromptAssemblyTier.HOT_SLIM,
        )
        self.assertEqual(
            brain_prompt_tier(use_slim_image=False, hot_path_slim=False),
            PromptAssemblyTier.FULL,
        )

    def test_snapshot_policy_terse(self):
        ctx = {"predictive_hint": {"terse_mode": True}, "group_id": None}
        s = snapshot_context_policy(ctx)
        self.assertTrue(s["terse_mode"])
        self.assertFalse(s["group_id_set"])


if __name__ == "__main__":
    unittest.main()
