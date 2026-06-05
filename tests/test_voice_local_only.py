import os
import unittest
from unittest import mock

from core.voice_module import VoiceModule


class VoiceLocalOnlyTests(unittest.TestCase):
    def test_local_only_skips_openrouter_when_backend_unset(self):
        env = {
            "VOICE_STT_LOCAL_ONLY": "true",
            "OPENROUTER_API_KEY": "sk-or-test",
            "VOICE_STT_BACKEND": "",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("VOICE_STT_BACKEND", None)
            v = VoiceModule()
        self.assertTrue(v.stt_local_only)
        self.assertEqual(v.stt_backend, "vosk")

    def test_explicit_backend_openrouter_still_used(self):
        env = {
            "VOICE_STT_LOCAL_ONLY": "true",
            "VOICE_STT_BACKEND": "openrouter",
            "OPENROUTER_API_KEY": "k",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            v = VoiceModule()
        self.assertEqual(v.stt_backend, "openrouter")

    def test_cloud_fallback_cleared_when_local_only(self):
        env = {
            "VOICE_STT_LOCAL_ONLY": "true",
            "VOICE_STT_FALLBACK_BACKEND": "openrouter",
            "OPENROUTER_API_KEY": "k",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            v = VoiceModule()
        self.assertEqual(v._stt_fallback, "")

    def test_auto_openrouter_fallback_when_vosk_and_key(self):
        env = {
            "VOICE_STT_BACKEND": "vosk",
            "OPENROUTER_API_KEY": "sk-or-test",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("VOICE_STT_FALLBACK_BACKEND", None)
            os.environ.pop("VOICE_STT_LOCAL_ONLY", None)
            os.environ.pop("VOICE_STT_AUTO_OPENROUTER_FALLBACK", None)
            v = VoiceModule()
        self.assertEqual(v._stt_fallback, "openrouter")
        self.assertTrue(v._stt_fallback_auto_openrouter)
        self.assertTrue(v.stt_status().get("stt_fallback_auto_openrouter"))

    def test_auto_openrouter_fallback_disabled(self):
        env = {
            "VOICE_STT_BACKEND": "vosk",
            "OPENROUTER_API_KEY": "sk-or-test",
            "VOICE_STT_AUTO_OPENROUTER_FALLBACK": "false",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("VOICE_STT_FALLBACK_BACKEND", None)
            v = VoiceModule()
        self.assertEqual(v._stt_fallback, "")
        self.assertFalse(v._stt_fallback_auto_openrouter)

    def test_explicit_fallback_not_flagged_auto(self):
        with mock.patch.dict(
            os.environ,
            {
                "VOICE_STT_BACKEND": "vosk",
                "VOICE_STT_FALLBACK_BACKEND": "openrouter",
                "OPENROUTER_API_KEY": "k",
            },
            clear=False,
        ):
            v = VoiceModule()
        self.assertEqual(v._stt_fallback, "openrouter")
        self.assertFalse(v._stt_fallback_auto_openrouter)

    def test_stt_empty_hint_local_only(self):
        env = {
            "VOICE_ENABLED": "true",
            "VOICE_STT_ENABLED": "true",
            "VOICE_STT_LOCAL_ONLY": "true",
            "OPENROUTER_API_KEY": "k",
            "VOICE_STT_BACKEND": "",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("VOICE_STT_BACKEND", None)
            v = VoiceModule()
            h = v.stt_empty_operator_hint()
        self.assertIn("vosk", v.stt_backend)
        self.assertIn("LOCAL_ONLY", h)
        self.assertIn("VOICE_STT_MODEL_PATH", h)


if __name__ == "__main__":
    unittest.main()
