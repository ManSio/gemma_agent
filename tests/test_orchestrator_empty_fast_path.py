import tempfile
import unittest

from core.models import Input
from core.orchestrator import Orchestrator
from core.plugin_registry import PluginRegistry
from core.policy_engine import PolicyEngine


class OrchestratorEmptyFastPathTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        pr = PluginRegistry(self._td.name)
        pe = PolicyEngine()
        self.o = Orchestrator(plugin_registry=pr, policy_engine=pe)

    def test_empty_text_no_attachment_uses_fallback_fast_path(self):
        inp = Input(type="text", payload="   ", meta={"trace_id": "trace-empty-fast"})
        plan = self.o.plan(inp, user_id="u1", group_id=None)
        self.assertEqual(len(plan.steps), 1)
        step = plan.steps[0]
        self.assertEqual(step.module_name, "__fallback__")
        self.assertEqual((step.args or {}).get("fallback_variant"), "empty_payload")
        ctx = (step.args or {}).get("context") or {}
        self.assertEqual(ctx.get("situation", {}).get("schema"), "situation_v1")
        self.assertEqual((ctx.get("dialogue_state") or {}).get("last_intent"), "empty")

    def test_whitespace_only_matches_empty_fast_path(self):
        inp = Input(type="text", payload="\n\t  \n", meta={})
        plan = self.o.plan(inp, user_id=None, group_id=None)
        self.assertEqual(plan.steps[0].module_name, "__fallback__")
        self.assertEqual((plan.steps[0].args or {}).get("fallback_variant"), "empty_payload")

    def test_image_attachment_skips_fast_path(self):
        inp = Input(
            type="text",
            payload="",
            meta={"file_context": {"file_type": "image", "local_path": "/tmp/x.png"}},
        )
        plan = self.o.plan(inp, user_id="u1", group_id=None)
        # Не ветка empty_no_attachment: либо другой модуль, либо fallback без empty_payload
        self.assertNotEqual((plan.steps[0].args or {}).get("fallback_variant"), "empty_payload")

    def test_attachment_flag_skips_empty_fast_path_without_file_context(self):
        inp = Input(
            type="file",
            payload="",
            meta={"has_telegram_attachment": True, "telegram_document_filename": "guide.pdf"},
        )
        plan = self.o.plan(inp, user_id="u1", group_id=None)
        self.assertNotEqual((plan.steps[0].args or {}).get("fallback_variant"), "empty_payload")


if __name__ == "__main__":
    unittest.main()
