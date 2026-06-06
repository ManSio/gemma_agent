"""
Тесты проверки сети/ключей: моки aiohttp, без реальных запросов.
"""
from __future__ import annotations

import asyncio
import json
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core import connectivity_check as cc
from core.connectivity_check import (
    MESSAGES,
    check_mem0_platform,
    check_openrouter_api,
    check_telegram_bot_token,
    get_external_connectivity_hints_for_health,
    run_connectivity_checks,
)


def _fake_session_for_get(resp_mock: MagicMock) -> MagicMock:
    get_ctx = MagicMock()
    get_ctx.__aenter__ = AsyncMock(return_value=resp_mock)
    get_ctx.__aexit__ = AsyncMock(return_value=None)

    sess = MagicMock()
    sess.get = MagicMock(return_value=get_ctx)
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=None)

    cs = MagicMock(return_value=sess)
    return cs


def _fake_session_for_post(resp_mock: MagicMock) -> MagicMock:
    post_ctx = MagicMock()
    post_ctx.__aenter__ = AsyncMock(return_value=resp_mock)
    post_ctx.__aexit__ = AsyncMock(return_value=None)

    sess = MagicMock()
    sess.post = MagicMock(return_value=post_ctx)
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=None)

    cs = MagicMock(return_value=sess)
    return cs


class ConnectivityCheckTests(unittest.IsolatedAsyncioTestCase):
    async def test_telegram_missing_token(self):
        r = await check_telegram_bot_token("")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error_code"], "missing_token")
        self.assertIn("TELEGRAM_TOKEN", r["user_message"])

    async def test_telegram_ok_mock(self):
        resp = MagicMock()
        resp.status = 200
        resp.json = AsyncMock(return_value={"ok": True, "result": {"id": 7, "username": "mybot"}})

        with patch.object(cc.aiohttp, "ClientSession", _fake_session_for_get(resp)):
            r = await check_telegram_bot_token("123:ABC")
        self.assertTrue(r["ok"])
        self.assertIn("mybot", r["user_message"])
        self.assertEqual(r["username"], "mybot")

    async def test_telegram_timeout_mock(self):
        async def aenter_timeout(*a, **k):
            raise asyncio.TimeoutError()

        get_ctx = MagicMock()
        get_ctx.__aenter__ = aenter_timeout
        get_ctx.__aexit__ = AsyncMock(return_value=None)

        sess = MagicMock()
        sess.get = MagicMock(return_value=get_ctx)
        sess.__aenter__ = AsyncMock(return_value=sess)
        sess.__aexit__ = AsyncMock(return_value=None)

        with patch.object(cc.aiohttp, "ClientSession", MagicMock(return_value=sess)):
            r = await check_telegram_bot_token("123:ABC")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error_code"], "timeout")
        self.assertIn("20", r["user_message"])

    async def test_openrouter_missing_key(self):
        r = await check_openrouter_api("")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error_code"], "missing_key")

    async def test_openrouter_ok_mock(self):
        body = {
            "choices": [{"message": {"content": "OK"}}],
        }
        resp = MagicMock()
        resp.status = 200
        resp.text = AsyncMock(return_value=json.dumps(body))

        with patch.object(cc.aiohttp, "ClientSession", _fake_session_for_post(resp)):
            r = await check_openrouter_api("sk-test", model="test/model")
        self.assertTrue(r["ok"])
        self.assertEqual(r["model"], "test/model")
        self.assertIn("OpenRouter", r["user_message"])

    async def test_openrouter_empty_content_mock(self):
        body = {"choices": [{"message": {"content": ""}}]}
        resp = MagicMock()
        resp.status = 200
        resp.text = AsyncMock(return_value=json.dumps(body))

        with patch.object(cc.aiohttp, "ClientSession", _fake_session_for_post(resp)):
            r = await check_openrouter_api("sk-test", model="m")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error_code"], "empty_content")

    async def test_openrouter_reasoning_fallback_counts_as_ok(self):
        body = {"choices": [{"message": {"content": "", "reasoning": "OK"}}]}
        resp = MagicMock()
        resp.status = 200
        resp.text = AsyncMock(return_value=json.dumps(body))

        with patch.object(cc.aiohttp, "ClientSession", _fake_session_for_post(resp)):
            r = await check_openrouter_api("sk-test", model="m")
        self.assertTrue(r["ok"])
        self.assertEqual(r.get("reply_preview"), "OK")

    async def test_openrouter_reasoning_content_fallback_counts_as_ok(self):
        body = {"choices": [{"message": {"content": None, "reasoning_content": " OK \n"}}]}
        resp = MagicMock()
        resp.status = 200
        resp.text = AsyncMock(return_value=json.dumps(body))

        with patch.object(cc.aiohttp, "ClientSession", _fake_session_for_post(resp)):
            r = await check_openrouter_api("sk-test", model="m")
        self.assertTrue(r["ok"])

    async def test_run_all_mocked(self):
        tg_resp = MagicMock()
        tg_resp.status = 200
        tg_resp.json = AsyncMock(return_value={"ok": True, "result": {"username": "x"}})

        or_body = {"choices": [{"message": {"content": "hi"}}]}
        or_resp = MagicMock()
        or_resp.status = 200
        or_resp.text = AsyncMock(return_value=json.dumps(or_body))

        n = {"i": 0}

        def session_factory(*a, **kw):
            n["i"] += 1
            sess = MagicMock()
            sess.__aenter__ = AsyncMock(return_value=sess)
            sess.__aexit__ = AsyncMock(return_value=None)
            if n["i"] == 1:
                tg_ctx = MagicMock()
                tg_ctx.__aenter__ = AsyncMock(return_value=tg_resp)
                tg_ctx.__aexit__ = AsyncMock(return_value=None)
                sess.get = MagicMock(return_value=tg_ctx)
            else:
                or_ctx = MagicMock()
                or_ctx.__aenter__ = AsyncMock(return_value=or_resp)
                or_ctx.__aexit__ = AsyncMock(return_value=None)
                sess.post = MagicMock(return_value=or_ctx)
            return sess

        with patch.object(cc.aiohttp, "ClientSession", MagicMock(side_effect=session_factory)):
            with patch.dict(
                os.environ,
                {
                    "MEM0_API_KEY": "",
                    "MEM0_MIRROR_API_KEY": "",
                    "CONNECTIVITY_SKIP_PLUGIN_HTTP_PROBES": "1",
                },
                clear=False,
            ):
                r = await run_connectivity_checks(telegram_token="t", openrouter_key="k")
        self.assertTrue(r["ok"])
        self.assertIn("summary", r)
        self.assertEqual(len(r["lines"]), 3)
        hints = get_external_connectivity_hints_for_health()
        self.assertTrue(hints.get("last_full_ok"))
        self.assertIn("telegram", hints.get("by_service", {}))

    async def test_run_all_with_mem0_local_probed(self):
        tg_resp = MagicMock()
        tg_resp.status = 200
        tg_resp.json = AsyncMock(return_value={"ok": True, "result": {"username": "x"}})

        or_body = {"choices": [{"message": {"content": "hi"}}]}
        or_resp = MagicMock()
        or_resp.status = 200
        or_resp.text = AsyncMock(return_value=json.dumps(or_body))

        m0_resp = MagicMock()
        m0_resp.status = 200
        m0_resp.text = AsyncMock(return_value="{}")

        n = {"i": 0}

        def session_factory(*a, **kw):
            n["i"] += 1
            sess = MagicMock()
            sess.__aenter__ = AsyncMock(return_value=sess)
            sess.__aexit__ = AsyncMock(return_value=None)
            if n["i"] == 1:
                tg_ctx = MagicMock()
                tg_ctx.__aenter__ = AsyncMock(return_value=tg_resp)
                tg_ctx.__aexit__ = AsyncMock(return_value=None)
                sess.get = MagicMock(return_value=tg_ctx)
            else:
                post_resp = or_resp if n["i"] == 2 else m0_resp
                p_ctx = MagicMock()
                p_ctx.__aenter__ = AsyncMock(return_value=post_resp)
                p_ctx.__aexit__ = AsyncMock(return_value=None)
                sess.post = MagicMock(return_value=p_ctx)
            return sess

        with patch.object(cc.aiohttp, "ClientSession", MagicMock(side_effect=session_factory)):
            with patch.dict(
                os.environ,
                {
                    "MEM0_API_KEY": "",
                    "MEM0_LOCAL": "true",
                    "MEM0_API_URL": "http://127.0.0.1:8001",
                    "MEM0_MIRROR_API_KEY": "",
                    "CONNECTIVITY_SKIP_PLUGIN_HTTP_PROBES": "1",
                },
                clear=False,
            ):
                r = await run_connectivity_checks(telegram_token="t", openrouter_key="k")
        self.assertTrue(r["ok"])
        mem0 = r.get("mem0") or {}
        self.assertFalse(mem0.get("skipped"))
        self.assertIn("self-hosted", (mem0.get("user_message") or "").lower())
        self.assertEqual(n["i"], 3)

    async def test_mem0_platform_404_then_simple_search_ok(self):
        """Как у Mem0MemoryModule: 404 на /v3/... → повтор на /search."""
        r404 = MagicMock()
        r404.status = 404
        r404.text = AsyncMock(return_value='{"detail":"Not Found"}')
        ctx404 = MagicMock()
        ctx404.__aenter__ = AsyncMock(return_value=r404)
        ctx404.__aexit__ = AsyncMock(return_value=None)

        r200 = MagicMock()
        r200.status = 200
        r200.text = AsyncMock(return_value="{}")
        ctx200 = MagicMock()
        ctx200.__aenter__ = AsyncMock(return_value=r200)
        ctx200.__aexit__ = AsyncMock(return_value=None)

        sess = MagicMock()
        sess.__aenter__ = AsyncMock(return_value=sess)
        sess.__aexit__ = AsyncMock(return_value=None)
        sess.post = MagicMock(side_effect=[ctx404, ctx200])

        with patch.object(cc.aiohttp, "ClientSession", MagicMock(return_value=sess)):
            with patch.dict(os.environ, {"MEM0_LOCAL_SIMPLE_COMPAT": "true"}, clear=False):
                r = await check_mem0_platform("local", api_url="http://127.0.0.1:8001", self_hosted=True)
        self.assertTrue(r.get("ok"))
        self.assertEqual(r.get("mem0_connectivity_path"), "simple_search")

    def test_messages_templates_format(self):
        """Шаблоны MESSAGES должны собираться без KeyError."""
        _ = MESSAGES["telegram_ok"].format(username="u")
        _ = MESSAGES["telegram_http_error"].format(status=400)
        _ = MESSAGES["telegram_timeout"].format(timeout=20)
        _ = MESSAGES["telegram_network"].format(detail="x")
        _ = MESSAGES["openrouter_ok"].format(model="m")
        _ = MESSAGES["openrouter_http"].format(status=401, detail="d")
        _ = MESSAGES["openrouter_timeout"].format(timeout=20)
        _ = MESSAGES["openrouter_network"].format(detail="e")
        _ = MESSAGES["summary_issues"].format(issues="a, b")
        _ = MESSAGES["mem0_skipped"]
        _ = MESSAGES["mem0_ok"].format(role="primary")
        _ = MESSAGES["mem0_ok_self_hosted"].format(role="primary")
        _ = MESSAGES["mem0_ok_simple_api"].format(role="primary")
        _ = MESSAGES["mem0_ok_self_hosted_simple"].format(role="primary")
        _ = MESSAGES["mem0_http"].format(role="primary", status=401, detail="d")
        _ = MESSAGES["mem0_timeout"].format(role="mirror", timeout=20)
        _ = MESSAGES["mem0_network"].format(role="primary", detail="e")


if __name__ == "__main__":
    unittest.main()
