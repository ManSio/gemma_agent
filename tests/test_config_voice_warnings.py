import os
import unittest
from unittest import mock

from core.config_manager import AppConfig


class ConfigVoiceWarningsTests(unittest.TestCase):
    def test_warns_openrouter_without_key(self):
        env = {
            "VOICE_ENABLED": "true",
            "VOICE_STT_ENABLED": "true",
            "VOICE_STT_BACKEND": "openrouter",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("OPENROUTER_API_KEY", None)
            cfg = AppConfig()
            r = cfg.validate()
        warns = " ".join(r.get("warnings") or [])
        self.assertIn("OPENROUTER_API_KEY", warns)

    def test_warns_openai_without_key(self):
        env = {
            "VOICE_ENABLED": "true",
            "VOICE_STT_ENABLED": "true",
            "VOICE_STT_BACKEND": "openai",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)
            os.environ.pop("VOICE_STT_API_KEY", None)
            cfg = AppConfig()
            r = cfg.validate()
        warns = " ".join(r.get("warnings") or [])
        self.assertIn("OPENAI_API_KEY", warns)

    def test_warns_vosk_without_model_path(self):
        env = {
            "VOICE_ENABLED": "true",
            "VOICE_STT_ENABLED": "true",
            "VOICE_STT_BACKEND": "vosk",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("VOICE_STT_MODEL_PATH", None)
            cfg = AppConfig()
            r = cfg.validate()
        warns = " ".join(r.get("warnings") or [])
        self.assertIn("VOICE_STT_MODEL_PATH", warns)


if __name__ == "__main__":
    unittest.main()
