import unittest
from unittest import mock

from aiogram.types import Chat, Message, User

from core.input_handlers.slash_button_dispatch import dispatch_slash_from_button
from tests.fixtures.telegram_test_ids import TEST_ADMIN_UID, TEST_BOT_UID


def _bot_msg() -> Message:
    return Message(
        message_id=1,
        date=0,
        chat=Chat(id=1, type="private"),
        from_user=User(id=int(TEST_BOT_UID), is_bot=True, first_name="Bot"),
        text="panel",
    )


class SlashButtonDispatchTests(unittest.TestCase):
    def test_admin_dispatch_uses_actor_not_bot(self):
        import asyncio

        layer = mock.Mock()
        layer._admin_module.is_admin = lambda uid: uid == TEST_ADMIN_UID
        msg = _bot_msg()

        with mock.patch(
            "core.input_handlers.slash_button_dispatch.try_dispatch_inline_slash",
            new_callable=mock.AsyncMock,
            return_value=False,
        ), mock.patch(
            "core.input_handlers.slash_button_dispatch.try_dispatch_admin_slash",
            new_callable=mock.AsyncMock,
            return_value=True,
        ) as admin_dispatch:
            ok = asyncio.run(
                dispatch_slash_from_button(
                    layer, msg, "/admin_reputation", actor_user_id=TEST_ADMIN_UID
                )
            )
        self.assertTrue(ok)
        admin_dispatch.assert_awaited_once()
