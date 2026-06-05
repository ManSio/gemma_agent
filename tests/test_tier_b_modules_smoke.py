"""Smoke: tier B модули — load + execute/help где есть execute."""
from __future__ import annotations

import importlib
import inspect
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "config" / "modules_catalog.json"


def _first_trigger(manifest: dict) -> str:
    for c in manifest.get("commands") or []:
        if isinstance(c, dict):
            t = (c.get("trigger") or c.get("name") or "").strip()
            if t:
                return t if t.startswith("/") else f"/{t}"
        elif isinstance(c, str) and c.strip():
            t = c.strip()
            return t if t.startswith("/") else f"/{t}"
    return ""


def _load_class(entrypoint: str):
    mod_name, cls_name = entrypoint.rsplit(":", 1)
    mod = importlib.import_module(mod_name)
    return getattr(mod, cls_name)


class TierBModulesSmokeTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        data = json.loads(CATALOG.read_text(encoding="utf-8"))
        cls.tier_b = [
            (name, meta)
            for name, meta in (data.get("modules") or {}).items()
            if meta.get("tier") == "B"
        ]
        assert len(cls.tier_b) >= 7, len(cls.tier_b)

    async def test_tier_b_load_or_execute(self) -> None:
        for name, meta in self.tier_b:
            folder = ROOT / "modules" / meta["folder"]
            manifest = json.loads((folder / "module.json").read_text(encoding="utf-8"))
            ep = manifest.get("entrypoint") or ""
            with self.subTest(module=name):
                cls = _load_class(ep)
                inst = cls()
                if not inspect.iscoroutinefunction(getattr(inst, "execute", None)):
                    continue
                trigger = _first_trigger(manifest)
                payload = trigger or f"/{name.replace('-', '_')}"
                out = await inst.execute(
                    {"input": {"payload": payload}, "context": {"user_id": "1"}}
                )
                if isinstance(out, list):
                    self.assertGreater(len(out), 0)
                    text = out[0].payload if hasattr(out[0], "payload") else str(out[0])
                else:
                    text = out.payload if hasattr(out, "payload") else str(out)
                self.assertTrue(str(text).strip(), f"empty payload for {name}")


if __name__ == "__main__":
    unittest.main()
