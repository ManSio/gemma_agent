import tempfile
import unittest

from core.orchestrator import Orchestrator
from core.plugin_registry import PluginRegistry
from core.policy_engine import PolicyEngine


class FloodGatingTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        pr = PluginRegistry(self._td.name)
        pe = PolicyEngine()
        self.o = Orchestrator(plugin_registry=pr, policy_engine=pe)

    def test_repeat_text_blocks(self):
        uid, cid = "u1", "c1"
        last = {}
        for j in range(self.o.max_same_text + 2):
            last = self.o.assess_flood_risk(
                user_id=uid,
                chat_id=cid,
                text="same_spam_line",
                is_group=False,
                is_command=False,
                is_bot_trigger_event=False,
            )
            if j < self.o.max_same_text:
                self.assertFalse(last.get("blocked"), msg=f"iter {j}")
        self.assertTrue(last.get("blocked"))

    def test_group_trigger_cooldown(self):
        self.o.group_cooldown_sec = 60.0
        uid, cid = "u2", "g2"
        r1 = self.o.assess_flood_risk(
            user_id=uid,
            chat_id=cid,
            text="/cmd",
            is_group=True,
            is_command=True,
            is_bot_trigger_event=True,
        )
        self.assertFalse(r1.get("blocked"))
        r2 = self.o.assess_flood_risk(
            user_id=uid,
            chat_id=cid,
            text="/cmd2",
            is_group=True,
            is_command=True,
            is_bot_trigger_event=True,
        )
        self.assertTrue(r2.get("blocked"))
        self.assertEqual(r2.get("reason"), "group_trigger_cooldown")


if __name__ == "__main__":
    unittest.main()
