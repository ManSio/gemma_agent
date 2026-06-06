import asyncio
import os
import unittest
from unittest.mock import patch

from core.universal_search_module import UniversalSearchModule
from modules.external_apis.service import ExternalAPIService


class UniversalSearchModuleTests(unittest.TestCase):
    def test_empty_query(self):
        async def _run():
            return await UniversalSearchModule().search("")

        r = asyncio.run(_run())
        self.assertFalse(r.get("ok"))
        self.assertIn("required", (r.get("error") or "").lower())

    def test_disabled(self):
        async def _run():
            os.environ["UNIVERSAL_SEARCH_ENABLED"] = "0"
            try:
                return await UniversalSearchModule().search("anything")
            finally:
                os.environ.pop("UNIVERSAL_SEARCH_ENABLED", None)

        r = asyncio.run(_run())
        self.assertFalse(r.get("ok"))

    def test_fallback_aggregated(self):
        async def fake_lookup(self, query: str, country: str = ""):
            return {
                "source": "wikipedia",
                "data": {"configured": True, "summary": "Stub wiki answer for tests."},
            }

        async def _run():
            os.environ.pop("TAVILY_API_KEY", None)
            os.environ.pop("BRAVE_SEARCH_API_KEY", None)
            with patch.object(ExternalAPIService, "lookup_or_fallback", new=fake_lookup):
                return await UniversalSearchModule().search("тестовый запрос")

        r = asyncio.run(_run())
        self.assertTrue(r.get("ok"))
        self.assertEqual(r.get("source"), "wikipedia")
        self.assertIn("Stub wiki", r.get("summary") or "")


if __name__ == "__main__":
    unittest.main()
