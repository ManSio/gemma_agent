import os
import tempfile
import unittest
from unittest.mock import patch

from core import access_gate as ag


class AccessGateTests(unittest.TestCase):
    def setUp(self) -> None:
        ag.reset_for_tests()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_disabled_everyone_allowed(self):
        with patch.dict(os.environ, {"USER_ACCESS_APPROVAL_REQUIRED": "false"}, clear=False):
            ag.reset_for_tests()
            self.assertEqual(ag.evaluate_private_user("1", is_admin=False), "allow")

    def test_enqueue_approve_flow(self):
        with patch.dict(
            os.environ,
            {"USER_ACCESS_APPROVAL_REQUIRED": "true", "RESILIENCE_RUNTIME_DIR": self.tmp.name},
            clear=False,
        ):
            ag.reset_for_tests()
            self.assertEqual(ag.evaluate_private_user("42", is_admin=False), "enqueue")
            self.assertTrue(ag.enqueue_pending("42", username="u", full_name="Test"))
            self.assertEqual(ag.evaluate_private_user("42", is_admin=False), "pending")
            ok, _ = ag.approve("42")
            self.assertTrue(ok)
            self.assertEqual(ag.evaluate_private_user("42", is_admin=False), "allow")

    def test_reject_blocks(self):
        with patch.dict(
            os.environ,
            {"USER_ACCESS_APPROVAL_REQUIRED": "true", "RESILIENCE_RUNTIME_DIR": self.tmp.name},
            clear=False,
        ):
            ag.reset_for_tests()
            ag.enqueue_pending("7", username=None, full_name="")
            ok, _ = ag.reject("7")
            self.assertTrue(ok)
            self.assertEqual(ag.evaluate_private_user("7", is_admin=False), "blocked")

    def test_default_on_when_env_key_omitted(self):
        env = dict(os.environ)
        env.pop("USER_ACCESS_APPROVAL_REQUIRED", None)
        env["RESILIENCE_RUNTIME_DIR"] = self.tmp.name
        with patch.dict(os.environ, env, clear=True):
            ag.reset_for_tests()
            self.assertEqual(ag.evaluate_private_user("99", is_admin=False), "enqueue")

    def test_guest_replies_increment_and_cap(self):
        with patch.dict(
            os.environ,
            {
                "USER_ACCESS_APPROVAL_REQUIRED": "true",
                "USER_ACCESS_GUEST_REPLY_QUOTA": "3",
                "RESILIENCE_RUNTIME_DIR": self.tmp.name,
            },
            clear=False,
        ):
            ag.reset_for_tests()
            ag.increment_guest_replies("5", 1)
            ag.increment_guest_replies("5", 5)
            self.assertEqual(ag.guest_replies_used("5"), 3)

    def test_approve_clears_guest_counter(self):
        with patch.dict(
            os.environ,
            {
                "USER_ACCESS_APPROVAL_REQUIRED": "true",
                "USER_ACCESS_GUEST_REPLY_QUOTA": "10",
                "RESILIENCE_RUNTIME_DIR": self.tmp.name,
            },
            clear=False,
        ):
            ag.reset_for_tests()
            ag.enqueue_pending("8", username=None, full_name="")
            ag.increment_guest_replies("8", 2)
            self.assertEqual(ag.guest_replies_used("8"), 2)
            ok, _ = ag.approve("8")
            self.assertTrue(ok)
            self.assertEqual(ag.guest_replies_used("8"), 0)


if __name__ == "__main__":
    unittest.main()
