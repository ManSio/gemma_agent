import unittest
from unittest.mock import AsyncMock, MagicMock

from core.telegram_progress import (
    telegram_progress_arm,
    telegram_progress_disarm,
    telegram_progress_pulse,
)


class TelegramProgressTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        telegram_progress_disarm()

    async def test_pulse_edits_when_armed(self) -> None:
        bot = MagicMock()
        bot.edit_message_text = AsyncMock()
        telegram_progress_arm(bot, 123, 456)
        await telegram_progress_pulse("Шаг 1", force=True)
        bot.edit_message_text.assert_awaited_once()
        telegram_progress_disarm()
        bot.edit_message_text.reset_mock()
        await telegram_progress_pulse("Шаг 2", force=True)
        bot.edit_message_text.assert_not_awaited()

    async def test_throttle_skips_second_pulse(self) -> None:
        bot = MagicMock()
        bot.edit_message_text = AsyncMock()
        telegram_progress_arm(bot, 1, 2)
        await telegram_progress_pulse("A", force=True)
        # сразу второй — слишком рано
        await telegram_progress_pulse("B", force=False)
        self.assertEqual(bot.edit_message_text.await_count, 1)

    async def test_pulse_skips_when_stream_delivered(self) -> None:
        bot = MagicMock()
        bot.edit_message_text = AsyncMock()
        telegram_progress_arm(bot, 123, 456)
        from core.telegram_stream_reply import _delivered

        _delivered.set("готовый ответ stream")
        try:
            await telegram_progress_pulse("⏳ Думаю…", force=True)
            bot.edit_message_text.assert_not_awaited()
        finally:
            _delivered.set(None)
            telegram_progress_disarm()

    async def test_same_stage_uses_refresh_gap(self) -> None:
        bot = MagicMock()
        bot.edit_message_text = AsyncMock()
        telegram_progress_arm(bot, 1, 2)
        await telegram_progress_pulse("A", force=True)
        await telegram_progress_pulse("A", force=False)
        self.assertEqual(bot.edit_message_text.await_count, 1)


if __name__ == "__main__":
    unittest.main()
