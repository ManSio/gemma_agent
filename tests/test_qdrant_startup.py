"""Qdrant startup validation (fail-fast vs soft skip)."""
import os
import unittest
from unittest.mock import MagicMock, patch

from core.qdrant_startup import (
    ensure_qdrant_at_startup,
    ensure_qdrant_collections,
    qdrant_startup_strict_enabled,
    require_qdrant_env,
)


class TestQdrantStartup(unittest.TestCase):
    def test_require_qdrant_env_missing_url(self):
        with patch.dict(os.environ, {"QDRANT_URL": "", "QDRANT_API_KEY": "k"}, clear=False):
            with self.assertRaises(ValueError):
                require_qdrant_env()

    def test_require_qdrant_env_ok(self):
        with patch.dict(
            os.environ,
            {"QDRANT_URL": "http://127.0.0.1:6333", "QDRANT_API_KEY": "secret"},
            clear=False,
        ):
            url, key = require_qdrant_env()
        self.assertEqual(url, "http://127.0.0.1:6333")
        self.assertEqual(key, "secret")

    def test_strict_default_true(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(qdrant_startup_strict_enabled())

    def test_strict_false_from_env(self):
        with patch.dict(os.environ, {"QDRANT_STARTUP_STRICT": "false"}, clear=False):
            self.assertFalse(qdrant_startup_strict_enabled())

    @patch("core.qdrant_startup.ensure_qdrant_collections")
    @patch("core.qdrant_startup.require_qdrant_env")
    def test_ensure_at_startup_raises_when_strict(self, mock_require, mock_ensure):
        mock_require.return_value = ("http://q", "key")
        mock_ensure.side_effect = RuntimeError("connection refused")
        with patch.dict(os.environ, {"QDRANT_STARTUP_STRICT": "true"}, clear=False):
            with self.assertRaises(RuntimeError) as ctx:
                ensure_qdrant_at_startup()
        self.assertIn("Qdrant startup check failed", str(ctx.exception))

    @patch("core.qdrant_startup.ensure_qdrant_collections")
    @patch("core.qdrant_startup.require_qdrant_env")
    def test_ensure_at_startup_soft_when_not_strict(self, mock_require, mock_ensure):
        mock_require.return_value = ("http://q", "key")
        mock_ensure.side_effect = RuntimeError("connection refused")
        with patch.dict(os.environ, {"QDRANT_STARTUP_STRICT": "false"}, clear=False):
            ensure_qdrant_at_startup()

    @patch("core.qdrant_http.QdrantHTTP")
    def test_ensure_collections_creates_missing(self, mock_cls):
        client = MagicMock()
        mock_cls.return_value = client
        coll_a = MagicMock()
        coll_a.name = "gemma_classifier_cache"
        client.get_collections.return_value.collections = [coll_a]
        ensure_qdrant_collections("http://q", "key", ["gemma_classifier_cache", "gemma_lessons_cache"])
        client.create_collection.assert_called_once()
