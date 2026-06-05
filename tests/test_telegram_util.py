import unittest
from unittest.mock import AsyncMock, MagicMock

from aiogram.exceptions import TelegramBadRequest

from core.telegram_util import (
    DEFAULT_CHUNK,
    _html_to_plain_fallback,
    answer_with_retry,
    chunk_text,
    reply_text_chunks,
    safe_callback_answer,
    soft_truncate_plain,
)


class TelegramUtilTests(unittest.TestCase):
    def test_chunk_single(self):
        self.assertEqual(chunk_text("abc", limit=100), ["abc"])

    def test_chunk_multi(self):
        s = "x" * (DEFAULT_CHUNK + 100)
        parts = chunk_text(s, limit=DEFAULT_CHUNK)
        self.assertEqual(len(parts), 2)
        self.assertTrue(parts[1].startswith("… часть 2/2"))

    def test_html_to_plain_fallback(self):
        s = _html_to_plain_fallback("<b>Hi</b> &amp; <code>x</code>")
        self.assertIn("Hi", s)
        self.assertIn("&", s)

    def test_soft_truncate_prefers_word_boundary(self):
        words = ["alpha"] * 120
        s = " ".join(words)
        out = soft_truncate_plain(s, 80)
        self.assertLessEqual(len(out), 80)
        self.assertTrue(out.endswith("…"))

    def test_html_to_plain_fallback_long_soft(self):
        words = ["слово"] * 900
        s = "<p>" + " ".join(words) + "</p>"
        out = _html_to_plain_fallback(s)
        self.assertLessEqual(len(out), 4090)
        self.assertTrue(out.endswith("…"))


class ReplyTextChunksAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_reply_markup_only_on_last_chunk(self):
        msg = MagicMock()
        msg.answer = AsyncMock()
        long_text = "a" * (DEFAULT_CHUNK + 50)
        kb = {"inline_keyboard": []}
        await reply_text_chunks(msg, long_text, reply_markup=kb)
        self.assertEqual(msg.answer.await_count, 2)
        first_call = msg.answer.call_args_list[0]
        last_call = msg.answer.call_args_list[1]
        self.assertNotIn("reply_markup", first_call.kwargs)
        self.assertEqual(last_call.kwargs.get("reply_markup"), kb)


class AnswerWithRetryAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_bad_request_entities_falls_back_plain_without_parse_mode(self):
        msg = MagicMock()
        bad = TelegramBadRequest(method="sendMessage", message="Can't find end tag blockquote")
        ok = None

        async def side_effect(text, **kwargs):
            nonlocal ok
            if kwargs.get("parse_mode"):
                raise bad
            ok = kwargs
            return None

        msg.answer = AsyncMock(side_effect=side_effect)
        await answer_with_retry(msg, "<blockquote>x", parse_mode="HTML")
        self.assertEqual(msg.answer.await_count, 2)
        self.assertIsNone(ok.get("parse_mode"))


class SafeCallbackAnswerAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_returns_true(self):
        cb = MagicMock()
        cb.answer = AsyncMock(return_value=None)
        ok = await safe_callback_answer(cb, "ok", show_alert=False)
        self.assertTrue(ok)
        cb.answer.assert_awaited_once()

    async def test_stale_query_returns_false(self):
        stale = TelegramBadRequest(
            method="answerCallbackQuery",
            message=(
                "Bad Request: query is too old and response timeout expired or query ID is invalid"
            ),
        )
        cb = MagicMock()
        cb.answer = AsyncMock(side_effect=stale)
        ok = await safe_callback_answer(cb)
        self.assertFalse(ok)

    async def test_other_bad_request_propagates(self):
        err = TelegramBadRequest(method="answerCallbackQuery", message="Bad Request: something else")
        cb = MagicMock()
        cb.answer = AsyncMock(side_effect=err)
        with self.assertRaises(TelegramBadRequest):
            await safe_callback_answer(cb)
