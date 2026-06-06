import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.types import Message

from core.input_handlers.inline_slash_dispatch import try_dispatch_inline_slash


class InlineSlashDispatchTests(unittest.IsolatedAsyncioTestCase):
    @patch("core.input_handlers.telegram_command_runners.run_me", new_callable=AsyncMock)
    async def test_me_uses_model_copy_with_effective_slash_text(self, run_me: AsyncMock):
        layer = MagicMock()
        msg = MagicMock(spec=Message)
        copied = MagicMock(spec=Message)
        msg.model_copy.return_value = copied

        ran = await try_dispatch_inline_slash(layer, msg, "/me")
        self.assertTrue(ran)
        msg.model_copy.assert_called_once_with(update={"text": "/me"})
        run_me.assert_awaited_once_with(layer, copied)

    async def test_unknown_slash_not_handled(self):
        layer = MagicMock()
        msg = MagicMock(spec=Message)
        ran = await try_dispatch_inline_slash(layer, msg, "/plugin_only_command_xyz")
        self.assertFalse(ran)
        msg.model_copy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
