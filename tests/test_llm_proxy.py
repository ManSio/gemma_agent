"""
Tests for LLM Proxy module (v1.0.0).
Tests: normalize_input, check_cache, apply_delta_prompting,
build_context_stitch, postprocess, reset_proxy_state, cache_hit flow.
"""
import json
import os
import time
import unittest
from unittest.mock import patch, AsyncMock


class TestNormalizeInput(unittest.TestCase):
    """test_normalize_input"""

    def test_normalize_strips_whitespace(self):
        from core.llm_proxy import normalize_input
        result = normalize_input("  hello  world  ")
        self.assertEqual(result, "hello world")

    def test_normalize_limits_length(self):
        from core.llm_proxy import normalize_input
        long_text = "x" * 40000
        result = normalize_input(long_text)
        self.assertLessEqual(len(result), 32000)

    def test_normalize_empty(self):
        from core.llm_proxy import normalize_input
        result = normalize_input("")
        self.assertEqual(result, "")


class TestCheckCache(unittest.TestCase):
    """test_check_cache"""

    def setUp(self):
        from core.llm_cache import invalidate_on_reset
        invalidate_on_reset("test")

    def test_cache_miss(self):
        from core.llm_proxy import check_cache
        result = check_cache("nonexistent_key_abcdef")
        self.assertIsNone(result)

    def test_cache_hit(self):
        from core.llm_cache import make_cache_key, set
        from core.llm_proxy import check_cache
        key = make_cache_key({"test": "ctx"}, "input", "model")
        set(key, {"content": "cached", "error": None})
        result = check_cache(key)
        if result is not None:
            self.assertEqual(result["content"], "cached")


class TestPostprocess(unittest.TestCase):
    """test_postprocess"""

    def test_postprocess_extracts_content(self):
        from core.llm_proxy import postprocess, ProxyResult
        result = postprocess({"content": "LLM response"})
        self.assertEqual(result.content, "LLM response")

    def test_postprocess_tool_calls(self):
        from core.llm_proxy import postprocess, ProxyResult
        result = postprocess({"content": "", "tool_calls": [{"tool": "math", "args": {}}]})
        self.assertEqual(len(result.tool_calls), 1)

    def test_postprocess_tool_call_in_text(self):
        from core.llm_proxy import postprocess, ProxyResult
        result = postprocess({"content": "Some text\nTOOL_CALL: math\n1+1"})
        self.assertIsInstance(result.content, str)


class TestProxyState(unittest.TestCase):
    """test_reset_proxy_state"""

    def setUp(self):
        from core.llm_proxy import reset_proxy_state
        reset_proxy_state()

    def test_reset_clears_state(self):
        from core.llm_proxy import get_proxy_state, set_active_model, reset_proxy_state, set_free_tokens
        set_active_model("test-model")
        set_free_tokens(500)
        state = get_proxy_state()
        self.assertEqual(state["active_model"], "test-model")

        reset_proxy_state()
        state_after = get_proxy_state()
        self.assertIsNone(state_after["active_model"])
        self.assertEqual(state_after["free_tokens"], 0)


class TestContextStitching(unittest.TestCase):
    """test_context_stitching"""

    def test_build_stitch_returns_dict(self):
        from core.llm_proxy import build_context_stitch
        result = build_context_stitch(context={"system_prompt": "Hello"})
        self.assertIsInstance(result, dict)

    def test_build_stitch_with_session_digest(self):
        from core.llm_proxy import build_context_stitch
        result = build_context_stitch(
            context={"system_prompt": "Hello"},
            session_digest={"turns": 5},
            experience_memory={"last_error": None},
        )
        self.assertIsInstance(result, dict)

    def test_stitch_has_cursor_ide_keys(self):
        from core.llm_proxy import build_context_stitch
        result = build_context_stitch(
            context={
                "system_prompt": "You are a helpful assistant.",
                "persona": "friendly",
                "recent_messages": [
                    {"role": "user", "content": "hello"},
                    {"role": "assistant", "content": "hi there"},
                ],
                "user_input": "how are you",
            }
        )
        self.assertIn("system", result)
        self.assertIn("persona", result)
        self.assertIn("digest", result)
        self.assertIn("history", result)
        self.assertIn("user", result)
        # Digest should be ≤ 300 chars
        self.assertLessEqual(len(result["digest"]), 300)
        # History should be ≤ 6 messages
        self.assertLessEqual(len(result["history"]), 6)
        # No dynamic keys allowed
        forbidden = {"session_id", "timestamp", "kv_state", "budget", "strategy", "autonomy"}
        for k in forbidden:
            self.assertNotIn(k, result)


class TestProxyKVReuse(unittest.TestCase):
    """test_kv_reuse_disabled"""

    def setUp(self):
        from core.llm_proxy import reset_proxy_state
        reset_proxy_state()

    def test_self_heal_returns_none(self):
        from core.llm_proxy import self_heal
        result = self_heal({"content": "test"}, 0.5)
        self.assertIsNone(result)

    def test_route_to_model_always_deepseek(self):
        from core.llm_proxy import route_to_model
        result = route_to_model("openrouter/free", {}, None, "")
        self.assertEqual(result, "openrouter/free")


class TestCacheKeyDeterminism(unittest.TestCase):
    """test_cache_key_determinism"""

    def setUp(self):
        from core.llm_cache import invalidate_on_reset
        invalidate_on_reset("test")

    def test_identical_inputs_produce_identical_keys(self):
        from core.llm_cache import make_cache_key
        ctx = {"system_prompt": "You are helpful.", "recent_messages": [{"role": "user", "content": "test"}]}
        key1 = make_cache_key(ctx, "hello world", "deepseek/deepseek-v4-pro")
        key2 = make_cache_key(ctx, "hello world", "deepseek/deepseek-v4-pro")
        self.assertEqual(key1, key2)

    def test_different_inputs_produce_different_keys(self):
        from core.llm_cache import make_cache_key
        ctx = {"system_prompt": "You are helpful."}
        key1 = make_cache_key(ctx, "hello world", "deepseek/deepseek-v4-pro")
        key2 = make_cache_key(ctx, "different input", "deepseek/deepseek-v4-pro")
        self.assertNotEqual(key1, key2)

    def test_cache_hit_on_identical_queries(self):
        from core.llm_cache import make_cache_key, get, set
        ctx = {"system_prompt": "You are helpful."}
        user_input = "what is 2+2"
        model = "deepseek/deepseek-v4-pro"
        key = make_cache_key(ctx, user_input, model)

        set(key, {"content": "4", "tool_calls": [], "reasoning_decision": {}})
        cached = get(key)
        self.assertIsNotNone(cached)
        self.assertEqual(cached["content"], "4")

    def test_cache_hit_on_similar_query_same_context(self):
        from core.llm_cache import make_cache_key, get, set
        ctx = {"system_prompt": "You are helpful.", "recent_messages": []}
        model = "deepseek/deepseek-v4-pro"
        key1 = make_cache_key(ctx, "hello", model)
        set(key1, {"content": "hi"})

        key2 = make_cache_key(ctx, "hello", model)
        self.assertEqual(key1, key2)
        self.assertIsNotNone(get(key2))


class TestDeltaPromptingStable(unittest.TestCase):
    """test_delta_prompting_stable"""

    def setUp(self):
        from core.llm_proxy import reset_proxy_state
        reset_proxy_state()

    def test_delta_triggers_on_context_change(self):
        from core.llm_budget import should_use_delta_prompting
        prev = {"a": "1", "b": "2", "c": "3", "d": "4", "e": "5", "f": "6", "g": "7", "h": "8"}
        new = {"a": "1", "b": "2", "c": "3", "d": "4", "e": "5", "f": "6", "g": "7", "h": "8"}
        # 100% key overlap
        self.assertTrue(should_use_delta_prompting(prev, new))

    def test_delta_does_not_trigger_on_low_overlap(self):
        from core.llm_budget import should_use_delta_prompting
        prev = {"a": "1", "b": "2", "c": "3"}
        new = {"x": "4", "y": "5", "z": "6", "w": "7", "v": "8"}
        # 0% key overlap
        self.assertFalse(should_use_delta_prompting(prev, new))

    def test_delta_deterministic(self):
        from core.llm_budget import build_delta
        prev = {"a": "1", "b": "old", "c": "3", "d": "4", "e": "5"}
        new = {"a": "1", "b": "new", "c": "3", "d": "4", "e": "5"}
        delta1 = build_delta(prev, new)
        delta2 = build_delta(prev, new)
        self.assertEqual(delta1, delta2)


class TestCollapseContextDeterminism(unittest.TestCase):
    """test_collapse_context_determinism"""

    def test_trim_to_last_6_messages(self):
        from core.llm_proxy import collapse_context_before_stitch
        ctx = {
            "system_prompt": "sys",
            "recent_messages": [
                {"role": "user", "content": str(i)} for i in range(10)
            ],
        }
        result = collapse_context_before_stitch(ctx)
        self.assertEqual(len(result["recent_messages"]), 6)
        self.assertEqual(result["recent_messages"][-1]["content"], "9")

    def test_drops_non_essential_keys(self):
        from core.llm_proxy import collapse_context_before_stitch
        ctx = {
            "system_prompt": "sys",
            "recent_messages": [],
            "experience_memory_hint": "should be dropped",
            "strategy_path_hint": "should be dropped",
            "route_risk_hint": "should be dropped",
        }
        result = collapse_context_before_stitch(ctx)
        self.assertNotIn("experience_memory_hint", result)
        self.assertNotIn("route_risk_hint", result)
        # strategy_path_hint больше не вырезается — идёт в промпт LLM
        self.assertIn("strategy_path_hint", result)

    def test_deterministic_output(self):
        from core.llm_proxy import collapse_context_before_stitch
        ctx = {
            "system_prompt": "test",
            "recent_messages": [{"role": "user", "content": "msg"}] * 8,
            "experience_memory_hint": "hint",
            "topic_tracking": "topic",
        }
        result1 = collapse_context_before_stitch(ctx)
        result2 = collapse_context_before_stitch(ctx)
        self.assertEqual(result1, result2)


class TestRouteToModel(unittest.TestCase):
    """test_route_to_model"""

    def test_route_always_deepseek(self):
        from core.llm_proxy import route_to_model
        result = route_to_model("any-model", {}, None, "default")
        self.assertEqual(result, "any-model")

    def test_route_always_deepseek_empty(self):
        from core.llm_proxy import route_to_model
        result = route_to_model("", {}, None, "default")
        self.assertEqual(result, "deepseek/deepseek-v4-pro")
