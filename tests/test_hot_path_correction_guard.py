"""Hot path slim rejects correction turns."""
from __future__ import annotations

import unittest

from core.brain.hot_path import _brain_slim_shared_rejects


class TestHotPathCorrectionGuard(unittest.TestCase):
    def test_correction_blocks_slim(self) -> None:
        ctx = {"correction_turn": True, "user_text": "не то"}
        self.assertTrue(
            _brain_slim_shared_rejects(
                user_text="не то",
                context=ctx,
                urls_chron=None,
                missing_facts=[],
                skill_name=None,
                skill_output=None,
                image_intent=None,
                group_transcript_compact="",
                group_chat_addon_len=0,
                group_in_groups_env="BRAIN_HOT_PATH_SLIM_IN_GROUPS",
            )
        )


if __name__ == "__main__":
    unittest.main()
