"""HTTP-level API guards: health, 501 stubs, request correlation id."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
from starlette.testclient import TestClient


def _api_test_env() -> None:
    os.environ["APP_ENV"] = "development"
    os.environ["API_ENABLED"] = "false"
    os.environ["API_TOKEN"] = "test-api-token-secret"
    os.environ["SECURITY_AES_KEY"] = Fernet.generate_key().decode()


class TestApiHttpGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _api_test_env()
        from api import app

        cls.client = TestClient(app)

    def test_health_returns_503_when_database_down(self) -> None:
        with patch("api.check_database_health", return_value={"ok": False, "error": "db down"}):
            resp = self.client.get("/api/v1/health")
        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertFalse(body.get("database_ok"))
        self.assertIn("database", " ".join(body.get("external_services_issues") or []))

    def test_legacy_health_returns_503_when_database_down(self) -> None:
        with patch("api.check_database_health", return_value={"ok": False, "error": "db down"}):
            resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 503)
        self.assertFalse(resp.json().get("database_ok"))

    def test_children_endpoint_returns_501(self) -> None:
        resp = self.client.get(
            "/api/v1/parents/p1/children",
            headers={"X-API-Token": "test-api-token-secret"},
        )
        self.assertEqual(resp.status_code, 501)
        self.assertEqual(resp.json().get("detail"), "Not implemented")

    def test_schedule_endpoint_returns_501(self) -> None:
        resp = self.client.get(
            "/api/v1/schedule/u1",
            headers={"X-API-Token": "test-api-token-secret"},
        )
        self.assertEqual(resp.status_code, 501)

    def test_request_id_header_echoed(self) -> None:
        with patch("api.check_database_health", return_value={"ok": True}):
            resp = self.client.get(
                "/api/v1/health",
                headers={"X-Request-Id": "trace-deadbeef"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("X-Request-Id"), "trace-deadbeef")

    def test_request_id_generated_when_missing(self) -> None:
        with patch("api.check_database_health", return_value={"ok": True}):
            resp = self.client.get("/api/v1/health")
        self.assertEqual(resp.status_code, 200)
        rid = resp.headers.get("X-Request-Id") or ""
        self.assertGreaterEqual(len(rid), 8)
