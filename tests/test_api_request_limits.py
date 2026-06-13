"""Unit tests for HTTP API request size limits."""
from __future__ import annotations

import json
import os
import unittest

from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from core.api_request_limits import (
    RequestBodySizeLimitMiddleware,
    api_message_max_chars,
    api_relay_meta_max_json_chars,
    validate_relay_meta,
)


async def _echo_post(request: Request) -> PlainTextResponse:
    await request.body()
    return PlainTextResponse("ok")


class TestApiRequestLimits(unittest.TestCase):
    def test_api_message_max_chars_default(self) -> None:
        os.environ.pop("API_MESSAGE_MAX_CHARS", None)
        self.assertEqual(api_message_max_chars(), 10000)

    def test_validate_relay_meta_accepts_small_payload(self) -> None:
        meta = {"k": "v"}
        self.assertEqual(validate_relay_meta(meta), meta)

    def test_validate_relay_meta_rejects_oversized_payload(self) -> None:
        os.environ["API_RELAY_META_MAX_JSON_CHARS"] = "64"
        limit = api_relay_meta_max_json_chars()
        huge = {"blob": "x" * limit}
        with self.assertRaises(ValueError):
            validate_relay_meta(huge)

    def test_request_body_size_limit_middleware_returns_413(self) -> None:
        max_bytes = 128
        app = Starlette(routes=[Route("/", _echo_post, methods=["POST"])])
        app.add_middleware(RequestBodySizeLimitMiddleware, max_bytes=max_bytes)
        with TestClient(app) as client:
            resp = client.post("/", content=b"x" * (max_bytes + 1))
        self.assertEqual(resp.status_code, 413)
        body = resp.json()
        self.assertIn("detail", body)

    def test_bot_relay_meta_validator_on_model(self) -> None:
        os.environ["APP_ENV"] = "development"
        os.environ["API_ENABLED"] = "false"
        os.environ["API_RELAY_META_MAX_JSON_CHARS"] = "32"
        from cryptography.fernet import Fernet

        os.environ["SECURITY_AES_KEY"] = Fernet.generate_key().decode()
        from api import BotRelayRequest

        with self.assertRaises(ValidationError):
            BotRelayRequest(user_id="u1", message="hi", meta={"x": "y" * 80})


class TestOpsProbeRequestLimits(unittest.TestCase):
    def test_ops_probe_message_max_length(self) -> None:
        from core.api_ops import OpsProbeRequest
        from core.api_request_limits import API_MESSAGE_MAX_CHARS

        req = OpsProbeRequest(user_id="u1", message="m" * API_MESSAGE_MAX_CHARS)
        self.assertEqual(len(req.message), API_MESSAGE_MAX_CHARS)
        with self.assertRaises(ValidationError):
            OpsProbeRequest(user_id="u1", message="m" * (API_MESSAGE_MAX_CHARS + 1))
