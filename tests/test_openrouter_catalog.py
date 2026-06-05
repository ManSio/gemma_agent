"""Каталог моделей OpenRouter: нормализация и кэш (без сети в основных тестах)."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.openrouter_catalog import (
    normalize_model_record,
    sort_models_for_display,
)


class OpenRouterCatalogTests(unittest.TestCase):
    def test_normalize_pricing_per_1m(self):
        m = {
            "id": "x/y",
            "name": "Test",
            "context_length": 8192,
            "pricing": {"prompt": "0.000001", "completion": "0.000002"},
        }
        n = normalize_model_record(m)
        self.assertEqual(n["prompt_usd_per_1m"], 1.0)
        self.assertEqual(n["completion_usd_per_1m"], 2.0)
        self.assertFalse(n["likely_free_route"])

    def test_normalize_freeish(self):
        m = {"id": "free/x", "pricing": {"prompt": "0", "completion": "0"}}
        n = normalize_model_record(m)
        self.assertTrue(n["likely_free_route"])

    def test_sort_prefers_free(self):
        rows = [
            {"id": "a", "likely_free_route": False, "context_length": 100},
            {"id": "b", "likely_free_route": True, "context_length": 50},
        ]
        s = sort_models_for_display(rows, prefer_free=True)
        self.assertEqual(s[0]["id"], "b")


class OpenRouterCatalogAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_catalog_uses_fetch(self):
        from core import openrouter_catalog as oc

        oc.invalidate_openrouter_models_cache()
        fake = [{"id": "m/1", "name": "M", "context_length": 100, "pricing": {"prompt": "0", "completion": "0"}}]

        async def fake_fetch(*, timeout_sec: float = 0):
            return fake

        with patch.object(oc, "fetch_openrouter_models_raw", fake_fetch):
            rows = await oc.get_openrouter_models_catalog(force_refresh=True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "m/1")


if __name__ == "__main__":
    unittest.main()
