"""memory_slash_bridge + plugin memory с Mem0 mock."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.memory_slash_bridge import forget_fact, recall_facts, remember_fact
from modules.memory.module import MemoryModule


class MemorySlashBridgeTests(unittest.TestCase):
    def test_remember_slash_only_without_user(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ok, backend = remember_fact(None, "факт-A", td)
            self.assertTrue(ok)
            self.assertEqual("slash_only", backend)
            lines, _ = recall_facts(None, td)
            self.assertIn("факт-A", lines)

    def test_remember_mem0_and_slash(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            mem = MagicMock()
            with patch("core.memory_slash_bridge._mem0_configured", return_value=True):
                with patch("core.brain.runtime.get_memory", return_value=mem):
                    ok, backend = remember_fact("42", "факт-B", td)
            self.assertTrue(ok)
            self.assertEqual("mem0", backend)
            mem.add_structured_facts.assert_called_once()
            self.assertTrue((Path(td) / "facts.json").is_file())

    async def _run(self, coro):
        return await coro

    def test_plugin_execute_with_context(self) -> None:
        import asyncio

        with tempfile.TemporaryDirectory() as td:
            mod = MemoryModule({"storage_path": td})
            mem = MagicMock()
            with patch("core.memory_slash_bridge._mem0_configured", return_value=True):
                with patch("core.brain.runtime.get_memory", return_value=mem):
                    out = asyncio.run(
                        mod.execute(
                            {
                                "input": {"payload": "/mem_remember из-TG"},
                                "context": {"user_id": "99"},
                            }
                        )
                    )
            self.assertTrue(out[0].meta.get("ok"))
            self.assertEqual("mem0", out[0].meta.get("backend"))

    def test_forget_mem0_and_slash(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            remember_fact("7", "удалить-меня", td)
            mem = MagicMock()
            mem.delete_facts_matching_text.return_value = 1
            with patch("core.memory_slash_bridge._mem0_configured", return_value=True):
                with patch("core.brain.runtime.get_memory", return_value=mem):
                    ok, backend, n = forget_fact("7", "удалить-меня", td)
            self.assertTrue(ok)
            self.assertGreaterEqual(n, 1)
            self.assertIn("mem0", backend)
            lines, _ = recall_facts("7", td)
            self.assertNotIn("удалить-меня", lines)


if __name__ == "__main__":
    unittest.main()
