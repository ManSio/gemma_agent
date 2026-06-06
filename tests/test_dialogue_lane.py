import os
import unittest
from unittest.mock import patch

from core.brain.dialogue_lane import is_direct_dialog_eligible


class DialogueLaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_patch = patch.dict(
            os.environ,
            {"BRAIN_DIRECT_DIALOG_ENABLED": "true"},
            clear=False,
        )
        self._env_patch.start()

    def tearDown(self) -> None:
        self._env_patch.stop()

    def test_eligible_simple_question(self):
        self.assertTrue(
            is_direct_dialog_eligible(
                "Почему трава зелёная?",
                brain_profile="quick_explain",
                task_facts={},
                translation_turn=False,
            )
        )

    def test_not_eligible_translation(self):
        self.assertFalse(
            is_direct_dialog_eligible(
                "Переведи на английский: hi",
                brain_profile="quick_explain",
                task_facts={},
                translation_turn=True,
            )
        )

    def test_not_eligible_weather(self):
        self.assertFalse(
            is_direct_dialog_eligible(
                "Какая погода в Минске?",
                brain_profile="standard",
                task_facts={"is_weather": True},
                translation_turn=False,
            )
        )

    def test_chat_agent_short_followup(self):
        with patch.dict(os.environ, {"BRAIN_CHAT_AGENT_MODE": "true"}, clear=False):
            self.assertTrue(
                is_direct_dialog_eligible(
                    "да",
                    brain_profile="standard",
                    task_facts={},
                    translation_turn=False,
                    recent_dialogue=[{"role": "user", "content": "новости"}],
                )
            )


if __name__ == "__main__":
    unittest.main()
