import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.bug_report_user import (
    bug_report_forward_recipient_ids,
    sanitize_user_bug_args,
    user_bug_cooldown_ok,
)


class BugReportUserPolicyTests(unittest.TestCase):
    def test_sanitize_strips_full_bundle(self):
        inc_n, n, comp, full, note = sanitize_user_bug_args(True, 80, "voice", True, "x")
        self.assertFalse(full)
        self.assertLessEqual(n, 100)

    def test_cooldown_blocks_second_call(self):
        with patch.dict("os.environ", {"BUG_REPORT_USER_COOLDOWN_SEC": "3600"}):
            ok1, _ = user_bug_cooldown_ok("999777")
            self.assertTrue(ok1)
            ok2, wait = user_bug_cooldown_ok("999777")
        self.assertFalse(ok2)
        self.assertGreater(wait, 0)


class BugReportUserFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_admin_bug_flow_forwards_zip_only_to_admins(self):
        from core.admin_bug_runner import run_admin_bug_flow

        layer = MagicMock()
        layer.orchestrator = MagicMock()
        layer._admin_module = MagicMock()
        layer._admin_module.admin_logs_snapshot = MagicMock(return_value={})
        layer._admin_module.is_admin = MagicMock(return_value=False)

        msg = MagicMock()
        msg.text = "/bug"
        msg.caption = None
        msg.message_id = 1
        msg.chat.id = 555
        msg.chat.type = "private"
        msg.from_user.id = 42
        msg.from_user.username = "reporter"
        msg.from_user.first_name = "U"
        msg.from_user.last_name = None
        msg.reply_to_message = None
        msg.answer = AsyncMock()
        msg.bot = AsyncMock()

        bundle = {"ok": True}
        zbytes = b"PK\x05\x06" + b"\x00" * 18

        mock_path_instance = MagicMock()
        mock_path_instance.__truediv__ = MagicMock(return_value=mock_path_instance)
        mock_path_instance.write_bytes = MagicMock()

        with (
            patch("core.admin_bug_runner.parse_admin_bug_command_args", return_value=(False, 40, None, False, None)),
            patch("core.admin_bug_runner.build_bug_report_document", return_value={}),
            patch("core.admin_bug_runner.build_diagnostic_bundle", new_callable=AsyncMock, return_value=bundle),
            patch("core.admin_bug_runner.admin_bug_report_zip_bytes", return_value=zbytes),
            patch("core.admin_bug_runner.copy_admin_zip_to_data_tools", return_value=None),
            patch("core.admin_bug_runner.bug_report_forward_recipient_ids", return_value=["100"]),
            patch("core.admin_bug_runner.Path", return_value=mock_path_instance),
        ):
            await run_admin_bug_flow(
                layer,
                msg,
                command_args=None,
                recent_chat_tail=[],
                capture_source="user_slash",
                zip_delivery="to_admins_only",
            )

        msg.bot.send_document.assert_awaited()
        self.assertEqual(msg.bot.send_document.await_args.kwargs["chat_id"], 100)
        msg.answer_document.assert_not_called()


if __name__ == "__main__":
    unittest.main()
