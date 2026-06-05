"""
Tests for LLM Patch Executor module (v1.0.0).
Tests: classify_repeated_errors, generate_patch, present_patch_to_admin,
determine_patch_strategy, format_patch_diff.
"""
import time
import unittest


class TestClassifyRepeatedErrors(unittest.TestCase):
    """test_classify_repeated_errors"""

    def test_empty_history(self):
        from core.llm_patch_executor import classify_repeated_errors
        result = classify_repeated_errors([])
        self.assertIsNone(result)

    def test_short_history(self):
        from core.llm_patch_executor import classify_repeated_errors
        result = classify_repeated_errors([{"error": "test"}])
        self.assertIsNone(result)

    def test_repeated_errors_detected(self):
        from core.llm_patch_executor import classify_repeated_errors
        history = [
            {"error": "timeout"},
            {"error": "timeout"},
            {"error": "timeout"},
        ]
        result = classify_repeated_errors(history)
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "repeated_error")
        self.assertEqual(result["error_pattern"], "timeout")
        self.assertGreaterEqual(result["occurrences"], 3)


class TestGeneratePatch(unittest.TestCase):
    """test_generate_patch"""

    def test_generate_patch_returns_valid_structure(self):
        from core.llm_patch_executor import generate_patch
        diff_context = {
            "type": "repeated_error",
            "error_pattern": "timeout",
            "occurrences": 3,
        }
        patch = generate_patch(diff_context)
        self.assertIn("patch_id", patch)
        self.assertIn("issue", patch)
        self.assertIn("strategy", patch)


class TestPresentPatch(unittest.TestCase):
    """test_present_patch_to_admin"""

    def test_present_patch_formatted(self):
        from core.llm_patch_executor import present_patch_to_admin
        patch = {
            "patch_id": "auto_test_123",
            "issue": {"type": "repeated_error", "error_pattern": "timeout", "occurrences": 3},
            "strategy": "reduce_reasoning_depth",
        }
        result = present_patch_to_admin(patch)
        self.assertIn("auto_test_123", result)
        self.assertIn("repeated_error", result)
        self.assertIn("reduce_reasoning_depth", result)


class TestDetermineStrategy(unittest.TestCase):
    """test_determine_patch_strategy"""

    def test_known_strategies(self):
        from core.llm_patch_executor import determine_patch_strategy
        self.assertEqual(determine_patch_strategy("runaway_latency", 2), "switch_to_free_model")
        self.assertEqual(determine_patch_strategy("invalid_json", 1), "add_json_format_prompt")
        self.assertEqual(determine_patch_strategy("bad_routing_empty", 1), "fallback_to_general")
        self.assertEqual(determine_patch_strategy("default", 1), "reduce_reasoning_depth")


class TestFormatDiff(unittest.TestCase):
    """test_format_patch_diff"""

    def test_format_diff(self):
        from core.llm_patch_executor import format_patch_diff
        patch = {
            "patch_id": "test_patch",
            "strategy": "test_strategy",
            "issue": {"error_pattern": "timeout", "occurrences": 3},
        }
        result = format_patch_diff(patch)
        self.assertIn("test_patch", result)
        self.assertIn("timeout", result)
