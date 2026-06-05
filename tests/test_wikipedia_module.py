import unittest
from unittest.mock import AsyncMock, patch

from core.wikipedia_module import WikipediaModule, _lang_from_wikipedia_url, _title_from_wikipedia_url


class WikipediaModuleTests(unittest.TestCase):
    def test_parse_url(self):
        self.assertEqual(
            _title_from_wikipedia_url("https://ru.wikipedia.org/wiki/Python"),
            "Python",
        )
        self.assertEqual(
            _title_from_wikipedia_url("https://en.wikipedia.org/wiki/Machine_learning"),
            "Machine learning",
        )
        self.assertIsNone(_title_from_wikipedia_url("https://example.com/wiki/X"))

    def test_lang_from_url(self):
        self.assertEqual(_lang_from_wikipedia_url("https://be.wikipedia.org/wiki/%D0%96%D0%B4%D0%B0%D0%BD%D0%BE%D0%B2%D1%96%D1%87%D1%8B"), "be")
        self.assertEqual(_lang_from_wikipedia_url("https://ru.wikipedia.org/wiki/Python"), "ru")
        self.assertIsNone(_lang_from_wikipedia_url("https://example.com"))

    def test_scan_via_url_mock(self):
        async def _run():
            body = {
                "query": {
                    "pages": {
                        "1": {
                            "title": "Python",
                            "extract": "Article body here.",
                        }
                    }
                }
            }
            with patch("modules.external_apis.clients._http_get_json", new=AsyncMock(return_value=(200, body))):
                return await WikipediaModule().scan("https://en.wikipedia.org/wiki/Python")

        import asyncio

        r = asyncio.run(_run())
        self.assertTrue(r.get("ok"))
        self.assertIn("Article body", r.get("text") or "")
        self.assertEqual(r.get("resolved_via"), "url")
        self.assertEqual(r.get("wiki_lang"), "en")

    def test_scan_lang_kwarg_uses_subdomain(self):
        async def _run():
            body = {
                "query": {
                    "pages": {
                        "1": {
                            "title": "Ждановічы",
                            "extract": "Вёска.",
                        }
                    }
                }
            }

            async def _capture(url: str):
                self.assertIn("//be.wikipedia.org/w/api.php", url)
                return 200, body

            with patch("modules.external_apis.clients._http_get_json", new=AsyncMock(side_effect=_capture)):
                return await WikipediaModule().scan("Ждановічы", lang="be")

        import asyncio

        r = asyncio.run(_run())
        self.assertTrue(r.get("ok"))
        self.assertEqual(r.get("wiki_lang"), "be")


if __name__ == "__main__":
    unittest.main()
