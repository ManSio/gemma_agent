import unittest
from unittest.mock import MagicMock

from core.group_transcript import (
    get_brain_extras,
    record_assistant_reply,
    record_triggered_user_turn,
)


class GroupTranscriptTests(unittest.TestCase):
    def test_assistant_and_commitment(self):
        import os
        import tempfile

        tmp = tempfile.mkdtemp()
        try:
            os.environ["BEHAVIOR_DATA_DIR"] = tmp
            os.environ["GROUP_TRANSCRIPT_PROMPT_LINES"] = "30"
            gid = "-100999001"

            record_assistant_reply(gid, "Ответ бота один")
            m = MagicMock()
            m.chat.id = int(gid)
            m.from_user.id = 42
            m.from_user.first_name = "Ира"
            m.from_user.last_name = ""
            m.from_user.username = None
            m.from_user.is_bot = False
            m.text = "бот, запомни завтра купить хлеб"
            m.caption = None
            m.photo = None
            m.document = None
            m.video = None
            m.voice = None

            record_triggered_user_turn(m, m.text)
            ex = get_brain_extras(gid)
            self.assertIn("хлеб", ex["commitments_hint"])
            self.assertIn("Ира", ex["transcript_compact"] or "")
            self.assertIn("бот", ex["transcript_compact"] or "")
            self.assertIn("Ира", ex.get("roster_hint") or "")
        finally:
            os.environ.pop("BEHAVIOR_DATA_DIR", None)
            os.environ.pop("GROUP_TRANSCRIPT_PROMPT_LINES", None)


if __name__ == "__main__":
    unittest.main()
