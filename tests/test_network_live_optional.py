"""
Интеграционные тесты сети (реальные HTTP). По умолчанию пропускаются.

  GEMMA_LIVE_NETWORK=1 pytest tests/test_network_live_optional.py -v
"""
from __future__ import annotations

import os
import unittest


@unittest.skipUnless((os.getenv("GEMMA_LIVE_NETWORK") or "").strip(), "Set GEMMA_LIVE_NETWORK=1 for live HTTP")
class NetworkLiveOptionalTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_fetch_openrouter_models_minimal(self):
        from core.openrouter_catalog import fetch_openrouter_models_raw

        rows = await fetch_openrouter_models_raw(timeout_sec=25.0)
        self.assertIsInstance(rows, list)
        self.assertGreaterEqual(len(rows), 5)
        for r in rows[:5]:
            self.assertIsInstance(r, dict)
            self.assertTrue(r.get("id"))

    async def test_live_http_probe_openrouter(self):
        from core.network_probe import http_get_roundtrip

        r = await http_get_roundtrip("https://openrouter.ai/api/v1/models", name="live", max_bytes=2048)
        self.assertTrue(r.get("ok"))
        self.assertGreater(r.get("roundtrip_ms") or 0, 0)


if __name__ == "__main__":
    unittest.main()
