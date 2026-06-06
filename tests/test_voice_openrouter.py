import json
import os
import unittest
from unittest.mock import mock_open, patch


class FakeOpenRouterResp:
    status = 200

    async def text(self):
        return json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": "текст с openrouter",
                        }
                    }
                ]
            }
        )


class FakePostCM:
    async def __aenter__(self):
        return FakeOpenRouterResp()

    async def __aexit__(self, *a):
        return None


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    def post(self, *a, **k):
        return FakePostCM()


class VoiceOpenRouterSTTTests(unittest.IsolatedAsyncioTestCase):
    async def test_openrouter_multimodal_parses_content(self):
        env = {
            "VOICE_ENABLED": "true",
            "VOICE_STT_ENABLED": "true",
            "VOICE_STT_BACKEND": "openrouter",
            "OPENROUTER_API_KEY": "sk-or-test",
            "VOICE_OPENROUTER_STT_MODEL": "google/gemini-2.0-flash-001",
        }
        with patch.dict(os.environ, env, clear=False):
            from core.voice_module import VoiceModule

            vm = VoiceModule()
            with patch("core.voice_module.aiohttp.ClientSession", return_value=FakeSession()):
                with patch("builtins.open", mock_open(read_data=b"\x00\x01\x02")):
                    out = await vm.stt("/tmp/x.ogg")
        self.assertEqual(out, "текст с openrouter")


if __name__ == "__main__":
    unittest.main()
