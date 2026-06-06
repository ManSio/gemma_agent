import os
import unittest
from unittest.mock import mock_open, patch


class FakeResp:
    status = 200

    async def text(self):
        return '{"text":"привет"}'


class FakePostCM:
    async def __aenter__(self):
        return FakeResp()

    async def __aexit__(self, *a):
        return None


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    def post(self, *a, **k):
        return FakePostCM()


class VoiceSTTOpenAITests(unittest.IsolatedAsyncioTestCase):
    async def test_stt_openai_parses_text(self):
        env = {
            "VOICE_ENABLED": "true",
            "VOICE_STT_ENABLED": "true",
            "VOICE_STT_BACKEND": "openai",
            "OPENAI_API_KEY": "sk-test",
        }
        with patch.dict(os.environ, env, clear=False):
            from core.voice_module import VoiceModule

            vm = VoiceModule()
            with patch("core.voice_module.aiohttp.ClientSession", return_value=FakeSession()):
                with patch("builtins.open", mock_open(read_data=b"\x00\x01")):
                    out = await vm.stt("/tmp/fake.ogg")
        self.assertEqual(out, "привет")


if __name__ == "__main__":
    unittest.main()
