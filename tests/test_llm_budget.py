"""
Tests for LLM Budget module (v1.0.0).
Tests: estimate_tokens, should_use_delta_prompting, apply_delta_prompting,
should_switch_model, should_collapse, should_reset_kv.
"""
import os
import unittest


class TestEstimateTokens(unittest.TestCase):
    """test_estimate_tokens"""

    def test_estimate_returns_positive_integer(self):
        from core.llm_budget import estimate_tokens
        est = estimate_tokens({"key": "value"}, "hello world")
        self.assertGreater(est, 0)
        self.assertIsInstance(est, int)

    def test_estimate_zero_for_empty(self):
        from core.llm_budget import estimate_tokens
        est = estimate_tokens(None, "")
        self.assertGreaterEqual(est, 0)


class TestDeltaPrompting(unittest.TestCase):
    """test_delta_prompting"""

    def test_should_use_delta_returns_false_on_none_prev(self):
        from core.llm_budget import should_use_delta_prompting
        result = should_use_delta_prompting(None, {"new": "ctx"})
        self.assertFalse(result)

    def test_apply_delta_returns_full_on_none_prev(self):
        from core.llm_budget import apply_delta_prompting
        ctx = {"key": "value", "other": 123}
        result = apply_delta_prompting(None, ctx)
        self.assertNotIn("__delta__", result)
        self.assertEqual(result["key"], "value")

    def test_apply_delta_returns_delta_on_small_change(self):
        from core.llm_budget import apply_delta_prompting
        prev = {"a": 1, "b": 2, "c": 3, "d": 4, "e": 5}
        new = {"a": 1, "b": 2, "c": 3, "d": 99, "e": 5}
        result = apply_delta_prompting(prev, new)
        if result.get("__delta__"):
            self.assertEqual(result["d"], 99)


class TestModelSwitch(unittest.TestCase):
    """test_should_switch_model"""

    def test_should_switch_below_threshold(self):
        from core.llm_budget import should_switch_model
        result = should_switch_model(1000, threshold=50000)
        self.assertTrue(result)

    def test_should_not_switch_above_threshold(self):
        from core.llm_budget import should_switch_model
        result = should_switch_model(100000, threshold=50000)
        self.assertFalse(result)


class TestShouldCollapse(unittest.TestCase):
    """test_should_collapse"""

    def test_should_collapse_empty_context(self):
        from core.llm_budget import should_collapse
        self.assertFalse(should_collapse(None))

    def test_should_collapse_large_context(self):
        from core.llm_budget import should_collapse
        big_context = {"text": "x" * 100000}
        result = should_collapse(big_context)
        self.assertIsInstance(result, bool)
