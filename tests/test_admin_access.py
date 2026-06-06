import unittest
from unittest.mock import AsyncMock, MagicMock

from core.input_handlers import admin_access as aa


class AdminAccessTests(unittest.TestCase):
    def test_admin_guard_prefers_effective_user_over_message_from_user(self):
        async def _go() -> bool:
            layer = MagicMock()
            layer._admin_module.is_admin = lambda uid: uid == "777"
            msg = MagicMock()
            msg.from_user = MagicMock()
            msg.from_user.id = 999001
            msg.answer = AsyncMock()
            with aa.effective_user_scope("777"):
                return await aa.admin_guard(msg, layer)

        import asyncio

        ok = asyncio.run(_go())
        self.assertTrue(ok)

    def test_admin_guard_falls_back_to_from_user_without_scope(self):
        async def _go() -> bool:
            layer = MagicMock()
            layer._admin_module.is_admin = lambda uid: uid == "42"
            msg = MagicMock()
            msg.from_user = MagicMock()
            msg.from_user.id = 42
            msg.answer = AsyncMock()
            return await aa.admin_guard(msg, layer)

        import asyncio

        ok = asyncio.run(_go())
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
