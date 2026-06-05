"""
Tests for LLM Cache module (v2.11.0).
Tests: make_cache_key, get, set, invalidate_on_reset, SQLite persistence.
"""
import json
import os
import time
import unittest
from unittest.mock import patch


class TestLLMCacheKey(unittest.TestCase):
    """test_make_cache_key"""

    def test_make_cache_key_returns_consistent_hash(self):
        from core.llm_cache import make_cache_key
        key1 = make_cache_key({"a": 1}, "hello", "model_x")
        key2 = make_cache_key({"a": 1}, "hello", "model_x")
        self.assertEqual(key1, key2)
        self.assertEqual(len(key1), 64)

    def test_make_cache_key_differs_on_input_change(self):
        from core.llm_cache import make_cache_key
        key1 = make_cache_key({"a": 1}, "hello", "model_x")
        key2 = make_cache_key({"a": 1}, "world", "model_x")
        self.assertNotEqual(key1, key2)


class TestLLMCacheGetSet(unittest.TestCase):
    """test_cache_get_set"""

    def setUp(self):
        os.environ["LLM_PROXY_CACHE_ENABLED"] = "true"
        from core.llm_cache import invalidate_on_reset
        invalidate_on_reset("test")

    def tearDown(self):
        os.environ.pop("LLM_PROXY_CACHE_ENABLED", None)

    def test_get_returns_stored_response(self):
        from core.llm_cache import make_cache_key, set, get
        key = make_cache_key({"ctx": "test"}, "input", "model")
        set(key, {"content": "cached response", "error": None})
        result = get(key)
        self.assertIsNotNone(result)
        self.assertEqual(result["content"], "cached response")

    def test_get_returns_none_on_miss(self):
        from core.llm_cache import get
        result = get("nonexistent_key_12345678")
        self.assertIsNone(result)

    def test_set_stores_error_response(self):
        from core.llm_cache import make_cache_key, set, get
        key = make_cache_key({"ctx": "test"}, "err", "model")
        set(key, {"content": "", "error": "some error"})
        result = get(key)
        self.assertIsNotNone(result)

    def test_set_stores_tool_call_response(self):
        from core.llm_cache import make_cache_key, set, get
        key = make_cache_key({"ctx": "test"}, "tool", "model")
        set(key, {"content": "TOOL_CALL: math", "error": None})
        result = get(key)
        self.assertIsNotNone(result)


class TestLLMCacheInvalidate(unittest.TestCase):
    """test_invalidate_on_reset"""

    def setUp(self):
        os.environ["LLM_PROXY_CACHE_ENABLED"] = "true"
        from core.llm_cache import invalidate_on_reset
        invalidate_on_reset("test")

    def tearDown(self):
        os.environ.pop("LLM_PROXY_CACHE_ENABLED", None)

    def test_invalidate_clears_sqlite_cache(self):
        from core.llm_cache import make_cache_key, set, get, invalidate_on_reset
        key = make_cache_key({"ctx": "test"}, "invalidate", "model")
        set(key, {"content": "will be cleared", "error": None})
        result_before = get(key)
        self.assertIsNotNone(result_before)

        invalidate_on_reset("test_reset")
        result_after = get(key)
        self.assertIsNone(result_after)


class TestLLMCacheDisable(unittest.TestCase):
    """test_cache_disabled"""

    def setUp(self):
        from core.llm_cache import invalidate_on_reset
        invalidate_on_reset("test")

    def test_get_returns_none_when_cache_disabled(self):
        from core.llm_cache import make_cache_key, set, get
        os.environ["LLM_PROXY_CACHE_ENABLED"] = "true"
        key = make_cache_key({"ctx": "test"}, "disabled", "model")
        set(key, {"content": "stored", "error": None})
        with patch("core.llm_cache._proxy_cache_enabled", return_value=False):
            result = get(key)
            self.assertIsNone(result)
        os.environ.pop("LLM_PROXY_CACHE_ENABLED", None)

    def test_set_skips_when_cache_disabled(self):
        from core.llm_cache import make_cache_key, set, get
        os.environ["LLM_PROXY_CACHE_ENABLED"] = "true"
        key = make_cache_key({"ctx": "test"}, "disabled_set", "model")
        with patch("core.llm_cache._proxy_cache_enabled", return_value=False):
            set(key, {"content": "should not store", "error": None})
        result = get(key)
        self.assertIsNone(result)
        os.environ.pop("LLM_PROXY_CACHE_ENABLED", None)
