import os
import unittest
from unittest.mock import AsyncMock, patch

from core.autopilot_cycle import cycle_enabled, run_cycle_once


class _Rc:
    def __init__(self, safe: bool = False):
        self._safe = safe
        self.cleared = False

    def is_enabled(self):
        return True

    def is_safe_mode(self):
        return self._safe

    def evaluate(self, _orch):
        return {"degraded": False, "critical": False, "error": None}

    def exit_safe_mode(self, _reason: str):
        self.cleared = True
        self._safe = False


class _Orch:
    def __init__(self, safe: bool = False):
        self._resilience = _Rc(safe=safe)


class _Bot:
    def __init__(self):
        self.send_message = AsyncMock(return_value=None)


class AutopilotCycleTests(unittest.IsolatedAsyncioTestCase):
    def test_cycle_enabled_by_autopilot(self):
        with patch.dict(os.environ, {"GEMMA_AUTOPILOT_MODE": "on"}, clear=False):
            self.assertTrue(cycle_enabled())

    async def test_cycle_once_returns_payload(self):
        with patch.dict(
            os.environ,
            {"GEMMA_AUTOPILOT_MODE": "on", "AUTOPILOT_REPORT_TO_ADMINS": "false"},
            clear=False,
        ):
            out = await run_cycle_once(_Orch(safe=False), bot=None)
        self.assertIn("xray", out)
        self.assertIn("recommendations", out)
        self.assertIn("actions", out)

    async def test_safe_mode_auto_clear_when_actions_enabled(self):
        with patch.dict(
            os.environ,
            {
                "GEMMA_AUTOPILOT_MODE": "on",
                "AUTOPILOT_ACTIONS_ENABLED": "true",
                "AUTOPILOT_REPORT_TO_ADMINS": "false",
            },
            clear=False,
        ):
            orch = _Orch(safe=True)
            out = await run_cycle_once(orch, bot=None)
        self.assertIn("safe_mode_cleared", out["actions"])

    async def test_notify_admins_if_enabled(self):
        with patch.dict(
            os.environ,
            {
                "GEMMA_AUTOPILOT_MODE": "on",
                "AUTOPILOT_REPORT_TO_ADMINS": "true",
                "AUTOPILOT_REPORT_COOLDOWN_SEC": "0",
                "ADMIN_USER_IDS": "123",
            },
            clear=False,
        ):
            bot = _Bot()
            await run_cycle_once(_Orch(safe=False), bot=bot)
        bot.send_message.assert_awaited()

    async def test_no_dm_when_report_env_unset(self):
        removed = os.environ.pop("AUTOPILOT_REPORT_TO_ADMINS", None)
        try:
            with patch.dict(
                os.environ,
                {"GEMMA_AUTOPILOT_MODE": "on", "ADMIN_USER_IDS": "123"},
                clear=False,
            ):
                bot = _Bot()
                await run_cycle_once(_Orch(safe=False), bot=bot)
                bot.send_message.assert_not_awaited()
        finally:
            if removed is not None:
                os.environ["AUTOPILOT_REPORT_TO_ADMINS"] = removed


if __name__ == "__main__":
    unittest.main()

