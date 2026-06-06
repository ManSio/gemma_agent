import os
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from core.autopilot_cycle import digest_enabled, maybe_run_idle_llm_probe, maybe_run_usage_digest


class AutopilotDigestProbeTests(unittest.IsolatedAsyncioTestCase):
    def test_digest_enabled_follows_cycle_when_unset(self):
        with patch.dict(os.environ, {"GEMMA_AUTOPILOT_MODE": "on"}, clear=False):
            self.assertTrue(digest_enabled())

    def test_digest_disabled_when_explicit_false(self):
        with patch.dict(
            os.environ,
            {"GEMMA_AUTOPILOT_MODE": "on", "AUTOPILOT_DIGEST_ENABLED": "false"},
            clear=False,
        ):
            self.assertFalse(digest_enabled())

    async def test_digest_sends_and_commits(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        bot = AsyncMock()
        bot.send_message = AsyncMock(return_value=None)
        with patch.dict(
            os.environ,
            {
                "GEMMA_AUTOPILOT_MODE": "on",
                "ADMIN_USER_IDS": "999",
                "RESILIENCE_RUNTIME_DIR": tmp,
            },
            clear=False,
        ):
            from core import usage_learning as ul

            ul.reset_for_tests()
            ul.record_usage("x", "a", "b")
            with patch(
                "core.usage_learning.should_emit_digest_this_hour",
                return_value=(True, "2026-05-01T08"),
            ):
                await maybe_run_usage_digest(bot)
            bot.send_message.assert_awaited()
            cp = ul.read_digest_checkpoint()
            self.assertEqual(cp.get("last_digest_slot"), "2026-05-01T08")

    async def test_idle_probe_skips_without_flag(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AUTOPILOT_IDLE_LLM_PROBE", None)
            bot = AsyncMock()
            await maybe_run_idle_llm_probe(bot)
        bot.send_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
