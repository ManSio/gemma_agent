"""Telegram polling must clear webhook before getUpdates (409 Conflict)."""
import unittest
from unittest.mock import AsyncMock, patch

from core.input_layer import InputLayer


class TestTelegramPolling(unittest.IsolatedAsyncioTestCase):
    async def test_start_polling_deletes_webhook_first(self):
        layer = InputLayer.__new__(InputLayer)
        layer.bot = AsyncMock()
        layer.dp = AsyncMock()
        layer.dp.start_polling = AsyncMock()

        with patch("core.input_layer.mark_boot"):
            await InputLayer.start_polling(layer)

        layer.bot.delete_webhook.assert_awaited_once_with(drop_pending_updates=True)
        layer.dp.start_polling.assert_awaited_once_with(layer.bot)
