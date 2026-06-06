import unittest
from unittest.mock import patch

from core.url_fetch import UrlFetchModule, _validate_http_url, safe_fetch_raw


class UrlFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_localhost(self):
        m = UrlFetchModule()
        out = await m.fetch_page("http://127.0.0.1/secret")
        self.assertIn("error", out)
        self.assertNotIn("ok", out)

    async def test_rejects_userinfo(self):
        m = UrlFetchModule()
        out = await m.fetch_page("http://user:pass@example.com/")
        self.assertIn("error", out)

    def test_validate_blocks_private_dns(self):
        with patch(
            "core.url_fetch.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("10.0.0.1", 0))],
        ):
            ok, err = _validate_http_url("http://example.com/")
        self.assertFalse(ok)
        self.assertIn("blocked", err)

    async def test_safe_fetch_mirror_fallback_on_403(self):
        calls: list[str] = []

        async def fake_once(u: str):
            calls.append(u)
            if "r.jina.ai" in u:
                return {
                    "ok": True,
                    "url": u,
                    "raw": b"<html><body>mirror ok</body></html>",
                    "content_type": "text/html; charset=utf-8",
                    "http_status": 200,
                }
            return {"error": "http 403", "url": u}

        with patch("core.url_fetch._safe_fetch_raw_once", side_effect=fake_once):
            with patch("core.url_fetch._validate_http_url", return_value=(True, "")):
                out = await safe_fetch_raw("https://blocked.example/page")
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("mirror_used"), "jina_reader")
        self.assertTrue(any("r.jina.ai" in c for c in calls))

    async def test_fetch_html_success_mocked(self):
        html = b"<html><head><title>Doc</title></head><body><p>Hello world</p></body></html>"

        class FakeContent:
            async def iter_chunked(self, n):
                yield html

        class FakeResp:
            status = 200
            url = "https://docs.example/page"
            headers = {"Content-Type": "text/html; charset=utf-8"}

            async def text(self):
                return ""

            @property
            def content(self):
                return FakeContent()

        class GetCM:
            def __init__(self):
                self._r = FakeResp()

            async def __aenter__(self):
                return self._r

            async def __aexit__(self, *a):
                return None

        class FakeSession:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            def get(self, url, allow_redirects=False):
                return GetCM()

        with patch("core.url_fetch.aiohttp.ClientSession", FakeSession):
            with patch("core.url_fetch._validate_http_url", return_value=(True, "")):
                m = UrlFetchModule()
                out = await m.fetch_page("https://docs.example/page")
        self.assertTrue(out.get("ok"))
        self.assertIn("Hello world", out.get("text", ""))
        self.assertEqual(out.get("title"), "Doc")


class ToolsWrapperAwaitTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_tool_awaits_async_module_method(self):
        from core import tools as tools_mod

        if "UrlFetch.fetch_page" not in tools_mod.TOOLS:
            self.skipTest("UrlFetch.fetch_page not in TOOLS")

        async def fake_fetch(url: str = "", user_id: str = ""):
            return {"ok": True, "tool": "UrlFetch.fetch_page"}

        orig = tools_mod.TOOLS["UrlFetch.fetch_page"]
        tools_mod.TOOLS["UrlFetch.fetch_page"] = fake_fetch
        try:
            out = await tools_mod.run_tool("UrlFetch.fetch_page", url="https://x.test")
        finally:
            tools_mod.TOOLS["UrlFetch.fetch_page"] = orig
        self.assertEqual(out.get("tool"), "UrlFetch.fetch_page")


if __name__ == "__main__":
    unittest.main()
