import unittest

from core.grounding_pack import build_minimal_grounding


class GroundingPackTests(unittest.TestCase):
    def test_voice_transcription_note_in_grounding(self):
        g = build_minimal_grounding({"telegram_voice_transcription": True}, {})
        self.assertIn("голос_STT", g)
        self.assertIn("заказ", g)

    def test_voice_flag_absent_by_default(self):
        g = build_minimal_grounding({}, {})
        self.assertNotIn("голос_STT", g)


if __name__ == "__main__":
    unittest.main()
