"""group_behavior: шаблонные ответы помечены явно."""
from __future__ import annotations

import json
import unittest

from modules.group_behavior.module import GroupBehaviorModule


class GroupBehaviorTemplateTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_reply_marked_template(self) -> None:
        mod = GroupBehaviorModule()
        ctx = {"group_type": "normal_group", "message": "нужна помощь с задачей"}
        out = await mod.execute(
            {
                "input": {
                    "payload": "/generate_group_reply " + json.dumps(ctx, ensure_ascii=False),
                }
            }
        )
        self.assertEqual(out[0].meta.get("reply_source"), "template")
        self.assertIn("не LLM", out[0].payload)
        self.assertIn("[шаблон]", out[0].payload)


if __name__ == "__main__":
    unittest.main()
