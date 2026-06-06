import unittest
from unittest import mock

from aiogram.types import Chat, Message, User

from core.input_handlers.admin_slash_dispatch import try_dispatch_admin_slash


from tests.fixtures.telegram_test_ids import TEST_ADMIN_UID, TEST_BOT_UID


def _msg(text: str, uid: int = int(TEST_ADMIN_UID)) -> Message:
    return Message(
        message_id=1,
        date=0,
        chat=Chat(id=uid, type="private"),
        from_user=User(id=uid, is_bot=False, first_name="A"),
        text=text,
    )


class EffectiveAdminUserIdTests(unittest.TestCase):
    def test_bot_message_uses_actor_scope(self):
        from core.input_handlers.admin_access import effective_admin_user_id, effective_user_scope

        msg = Message(
            message_id=1,
            date=0,
            chat=Chat(id=1, type="private"),
            from_user=User(id=int(TEST_BOT_UID), is_bot=True, first_name="Bot"),
            text="x",
        )
        self.assertEqual(effective_admin_user_id(msg), TEST_BOT_UID)
        with effective_user_scope(TEST_ADMIN_UID):
            self.assertEqual(effective_admin_user_id(msg), TEST_ADMIN_UID)
        self.assertEqual(effective_admin_user_id(msg, "999"), "999")


class AdminSlashDispatchTests(unittest.TestCase):
    def test_unknown_token_returns_false(self):
        layer = mock.Mock()
        msg = _msg("/admin_unknown_xyz")
        with mock.patch(
            "core.input_handlers.admin_slash_dispatch.admin_guard",
            new_callable=mock.AsyncMock,
            return_value=True,
        ):
            import asyncio

            ok = asyncio.run(try_dispatch_admin_slash(layer, msg, "/admin_unknown_xyz"))
        self.assertFalse(ok)

    def test_admin_reputation_dispatched(self):
        import asyncio

        from core.input_handlers import admin_slash_dispatch as mod

        layer = mock.Mock()
        msg = _msg("/admin_reputation")
        runner = mock.AsyncMock()
        with mock.patch.object(mod, "admin_guard", new_callable=mock.AsyncMock, return_value=True), mock.patch.dict(
            mod._DISPATCH, {"admin_reputation": runner}, clear=False
        ):
            ok = asyncio.run(mod.try_dispatch_admin_slash(layer, msg, "/admin_reputation"))
        self.assertTrue(ok)
        runner.assert_awaited_once()

    def test_admin_reputation_uses_actor_not_bot(self):
        import asyncio

        from core.input_handlers import admin_slash_dispatch as mod
        from core.input_handlers.admin_access import effective_user_scope

        layer = mock.Mock()
        msg = Message(
            message_id=1,
            date=0,
            chat=Chat(id=1, type="private"),
            from_user=User(id=int(TEST_BOT_UID), is_bot=True, first_name="Bot"),
            text="/admin_reputation",
        )
        captured: dict = {}

        async def capture(_layer, _msg, args):
            captured["uid"] = mod.effective_admin_user_id(_msg, args)

        with mock.patch.object(mod, "admin_guard", new_callable=mock.AsyncMock, return_value=True), mock.patch.dict(
            mod._DISPATCH, {"admin_reputation": capture}, clear=False
        ):
            with effective_user_scope(TEST_ADMIN_UID):
                ok = asyncio.run(mod.try_dispatch_admin_slash(layer, msg, "/admin_reputation"))
        self.assertTrue(ok)
        self.assertEqual(captured.get("uid"), TEST_ADMIN_UID)

    def test_memory_insight_parses_args(self):
        import asyncio

        from core.input_handlers import admin_slash_dispatch as mod

        layer = mock.Mock()
        msg = _msg("/admin_memory_insight 20")
        runner = mock.AsyncMock()
        with mock.patch.object(mod, "admin_guard", new_callable=mock.AsyncMock, return_value=True), mock.patch.dict(
            mod._DISPATCH, {"admin_memory_insight": runner}, clear=False
        ):
            ok = asyncio.run(mod.try_dispatch_admin_slash(layer, msg, "/admin_memory_insight 20"))
        self.assertTrue(ok)
        runner.assert_awaited_once()
        self.assertEqual(runner.await_args[0][2], "20")

    def test_memory_insight_scope_limit_keeps_user_id(self):
        from core.input_handlers import admin_slash_dispatch as mod

        n, uid, _gid = mod._memory_insight_scope(_msg("x"), "20")
        self.assertEqual(n, 20)
        self.assertEqual(uid, TEST_ADMIN_UID)

    def test_memory_insight_scope_telegram_id_not_limit(self):
        from core.input_handlers import admin_slash_dispatch as mod

        n, uid, _gid = mod._memory_insight_scope(_msg("x"), TEST_ADMIN_UID)
        self.assertEqual(n, 15)
        self.assertEqual(uid, TEST_ADMIN_UID)

    def test_memory_insight_scope_bot_message_uses_actor(self):
        from core.input_handlers import admin_slash_dispatch as mod
        from core.input_handlers.admin_access import effective_user_scope

        msg = Message(
            message_id=1,
            date=0,
            chat=Chat(id=1, type="private"),
            from_user=User(id=int(TEST_BOT_UID), is_bot=True, first_name="Bot"),
            text="x",
        )
        with effective_user_scope(TEST_ADMIN_UID):
            n, uid, _gid = mod._memory_insight_scope(msg, "15")
        self.assertEqual(n, 15)
        self.assertEqual(uid, TEST_ADMIN_UID)


class HelpStatsPageTests(unittest.TestCase):
    def test_stats_page_has_hs_buttons(self):
        from core.input_handlers.help_payload import build_help_payload
        from core.plugin_registry import PluginRegistry

        reg = PluginRegistry()
        with mock.patch.object(reg, "get_modules", return_value=[]):
            _, kb = build_help_payload(plugin_registry=reg, is_admin=True, page="admin_stats_page")
        flat = [b.callback_data for row in (kb.inline_keyboard or []) for b in row]
        self.assertIn("help:admin_stats_page", flat)
        self.assertTrue(any(str(x).startswith("hs:") for x in flat))
