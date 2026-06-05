import os
import unittest
from unittest.mock import patch

from core.dialog_recall_tool import DialogRecallModule


class DialogRecallToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_recall_bundle_calls_facade(self):
        mod = DialogRecallModule()
        with patch.dict(
            os.environ,
            {"DIALOG_MEMORY_RECALL_ENABLED": "true", "DIALOG_RECALL_BRAIN_TOOL_ENABLED": "true"},
            clear=False,
        ):
            with patch(
                "core.memory_recall_facade.build_slash_recall_bundle",
                return_value="BUNDLE_OK",
            ) as bm:
                out = await mod.recall_bundle(
                    user_id="9",
                    mode="summary",
                    recall_context={"dialogue_summary": "x"},
                )
        self.assertEqual(out.get("text"), "BUNDLE_OK")
        self.assertEqual(out.get("mode"), "summary")
        bm.assert_called_once()
        ca = bm.call_args.kwargs
        self.assertEqual(ca["user_id"], "9")
        self.assertEqual(ca["context"].get("dialogue_summary"), "x")

    async def test_disabled_by_env(self):
        mod = DialogRecallModule()
        with patch.dict(os.environ, {"DIALOG_RECALL_BRAIN_TOOL_ENABLED": "false"}, clear=False):
            out = await mod.recall_bundle(user_id="1")
        self.assertIn("error", out)


if __name__ == "__main__":
    unittest.main()
