import asyncio
import os
import unittest
from unittest.mock import patch

from core.input_layer import InputLayer


class PipelineChatLockTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_chat_default_is_lock_not_semaphore(self):
        layer = InputLayer.__new__(InputLayer)
        layer._pipeline_chat_locks = {}
        layer._pipeline_chat_locks_guard = asyncio.Lock()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TELEGRAM_PIPELINE_PRIVATE_PARALLEL", None)
            lock = await layer._pipeline_lock_for_chat("123", is_private=True)
        self.assertIsInstance(lock, asyncio.Lock)

    async def test_private_chat_semaphore_when_env_set(self):
        layer = InputLayer.__new__(InputLayer)
        layer._pipeline_chat_locks = {}
        layer._pipeline_chat_locks_guard = asyncio.Lock()
        with patch.dict(os.environ, {"TELEGRAM_PIPELINE_PRIVATE_PARALLEL": "2"}):
            lock = await layer._pipeline_lock_for_chat("456", is_private=True)
        self.assertIsInstance(lock, asyncio.Semaphore)


if __name__ == "__main__":
    unittest.main()
