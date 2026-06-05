"""Тесты TaskScout: JSON-план и локальные playbooks."""
from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, patch

from core import task_scout_module as tsm


class TaskScoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_scout_plan_parses_json(self):
        with patch.dict("os.environ", {"TASK_SCOUT_PLAYBOOK_PATH": ""}, clear=False):
            raw_plan = {
                "goal_restated": "Скачать документ",
                "steps": [{"step": 1, "action_type": "fetch", "detail": "UrlFetch"}],
                "risks_and_limits": "Нет браузера",
                "defense_overview": "WAF",
                "tools_suggested": ["UrlFetch.fetch_page"],
                "mem0_context_used": False,
            }

            async def fake_gen(*_a, **_k):
                return {"content": json.dumps(raw_plan, ensure_ascii=False)}

            with patch("core.task_scout_module.get_openrouter_provider") as gp:
                prov = gp.return_value
                prov.generate = AsyncMock(side_effect=fake_gen)
                with patch("core.brain.runtime._memory") as mem:
                    mem.on_before_response = AsyncMock(return_value=[])
                    mod = tsm.TaskScoutModule()
                    out = await mod.scout_plan(
                        goal="тест", user_id="u1", urls="https://example.com/x"
                    )
            self.assertEqual(out.get("goal_restated"), "Скачать документ")
            self.assertEqual(out.get("meta", {}).get("domains_guessed"), ["example.com"])

    async def test_save_and_recall_playbook(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            path = __import__("os").path.join(d, "pb.jsonl")
            with patch.dict("os.environ", {"TASK_SCOUT_PLAYBOOK_PATH": path}):
                mod = tsm.TaskScoutModule()
                r = await mod.save_playbook_note(
                    domain="example.com",
                    title="Заметка",
                    body="Текст",
                    user_id="u1",
                    tags="t1",
                )
                self.assertTrue(r.get("ok"))
                rec = await mod.recall_playbooks(domain="example.com", query="Заметка", limit=5)
                self.assertGreaterEqual(rec.get("count", 0), 1)

    async def test_scout_disabled(self):
        with patch.dict("os.environ", {"TASK_SCOUT_ENABLED": "false"}):
            self.assertFalse(tsm.scout_enabled())
            mod = tsm.TaskScoutModule()
            out = await mod.scout_plan(goal="x")
            self.assertTrue(out.get("skipped"))


if __name__ == "__main__":
    unittest.main()
