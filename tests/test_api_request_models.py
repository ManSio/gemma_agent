"""Pydantic limits on HTTP API chat payloads."""
from __future__ import annotations

import os
import unittest

from cryptography.fernet import Fernet
from pydantic import ValidationError

from core.api_request_limits import API_MESSAGE_MAX_CHARS


class TestApiRequestModels(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ["APP_ENV"] = "development"
        os.environ["API_ENABLED"] = "false"
        os.environ["SECURITY_AES_KEY"] = Fernet.generate_key().decode()
        from api import BotRelayRequest, ChatRequest

        cls.ChatRequest = ChatRequest
        cls.BotRelayRequest = BotRelayRequest

    def test_chat_request_accepts_max_message(self) -> None:
        req = self.ChatRequest(user_id="u1", message="x" * API_MESSAGE_MAX_CHARS)
        self.assertEqual(len(req.message), API_MESSAGE_MAX_CHARS)

    def test_chat_request_rejects_oversized_message(self) -> None:
        with self.assertRaises(ValidationError):
            self.ChatRequest(user_id="u1", message="x" * (API_MESSAGE_MAX_CHARS + 1))

    def test_bot_relay_request_accepts_max_message(self) -> None:
        req = self.BotRelayRequest(user_id="u1", message="y" * API_MESSAGE_MAX_CHARS)
        self.assertEqual(len(req.message), API_MESSAGE_MAX_CHARS)

    def test_bot_relay_request_rejects_oversized_message(self) -> None:
        with self.assertRaises(ValidationError):
            self.BotRelayRequest(user_id="u1", message="y" * (API_MESSAGE_MAX_CHARS + 1))

    def test_bot_relay_request_rejects_oversized_meta(self) -> None:
        os.environ["API_RELAY_META_MAX_JSON_CHARS"] = "48"
        with self.assertRaises(ValidationError):
            self.BotRelayRequest(user_id="u1", message="ok", meta={"payload": "z" * 80})
