"""Slash-store modules/memory (отдельно от Mem0 в brain)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from modules.memory.module import MemoryModule


class MemoryPluginModuleTests(unittest.IsolatedAsyncioTestCase):
    async def test_remember_recall_forget_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            mod = MemoryModule({"storage_path": td})
            out = await mod.execute(
                {
                    "input": {"payload": "/mem_remember тест-факт"},
                    "context": {"user_id": "1"},
                }
            )
            self.assertTrue(out[0].meta.get("ok"))
            out2 = await mod.execute({"input": {"payload": "/mem_recall"}})
            self.assertIn("тест-факт", out2[0].payload)
            out3 = await mod.execute({"input": {"payload": "/mem_forget тест-факт"}})
            self.assertTrue(out3[0].meta.get("ok"))
            facts_path = Path(td) / "facts.json"
            data = json.loads(facts_path.read_text(encoding="utf-8"))
            self.assertEqual([], data)

    async def test_remember_empty_payload(self) -> None:
        mod = MemoryModule({"storage_path": tempfile.mkdtemp()})
        out = await mod.execute({"input": {"payload": "/mem_remember "}})
        self.assertFalse(out[0].meta.get("ok"))


if __name__ == "__main__":
    unittest.main()
