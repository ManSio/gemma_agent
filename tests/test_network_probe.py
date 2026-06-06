"""Тесты HTTP-зондов сети (моки)."""
from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core import network_probe as np


class NetworkProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_get_roundtrip_ok(self):
        resp = MagicMock()
        resp.status = 200
        resp.content.read = AsyncMock(return_value=b'{"data":[]}')

        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=resp)
        ctx.__aexit__ = AsyncMock(return_value=None)

        sess = MagicMock()
        sess.get = MagicMock(return_value=ctx)
        sess.__aenter__ = AsyncMock(return_value=sess)
        sess.__aexit__ = AsyncMock(return_value=None)

        with patch.object(np.aiohttp, "ClientSession", MagicMock(return_value=sess)):
            r = await np.http_get_roundtrip("https://example.com/x", name="t")
        self.assertTrue(r.get("ok"))
        self.assertEqual(r.get("name"), "t")
        self.assertIsNotNone(r.get("roundtrip_ms"))

    async def test_run_http_latency_probes_gather(self):
        async def fake_probe(*a, **k):
            return {"name": k.get("name"), "ok": True, "roundtrip_ms": 1.0}

        with patch.object(np, "http_get_roundtrip", new=fake_probe):
            bundle = await np.run_http_latency_probes(include_plugin_endpoints=False)
        self.assertEqual(bundle.get("label"), "http_latency_probes")
        self.assertGreaterEqual(len(bundle.get("results") or []), 1)

    def test_collect_plugin_specs_respects_env(self):
        with patch.dict(
            os.environ,
            {
                "SEARXNG_INSTANCE_URL": "https://sx.example",
                "QDRANT_URL": "",
                "TAVILY_API_KEY": "",
                "BRAVE_SEARCH_API_KEY": "",
                "VOICE_STT_API_URL": "",
                "URL_FETCH_MIRROR_BASE": "",
                "CONNECTIVITY_EXTRA_HTTP_PROBES": "",
            },
            clear=False,
        ):
            specs = np.collect_plugin_http_probe_specs()
        names = [s.get("name") for s in specs]
        self.assertIn("searxng_search", names)

    async def test_run_plugin_http_probes_empty_specs(self):
        bundle = await np.run_plugin_http_probes(specs=[])
        self.assertEqual(bundle.get("results"), [])


if __name__ == "__main__":
    unittest.main()
