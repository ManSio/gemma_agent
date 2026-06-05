"""
Tests for LLM Self-Healing module (v1.0.0).
Tests: detect_runaway_reasoning, detect_invalid_json, detect_tool_error,
detect_bad_routing, apply_recovery_strategy, switch_model_on_failure,
reset_context_on_failure.
"""
import time
import unittest


class TestDetectRunaway(unittest.TestCase):
    """test_detect_runaway_reasoning"""

    def test_detect_none_response(self):
        from core.llm_self_heal import detect_runaway_reasoning
        self.assertFalse(detect_runaway_reasoning(None, 1.0))

    def test_detect_latency_exceeded(self):
        from core.llm_self_heal import detect_runaway_reasoning
        result = detect_runaway_reasoning({"content": "short"}, 200.0)
        self.assertTrue(result)

    def test_detect_short_latency_ok(self):
        from core.llm_self_heal import detect_runaway_reasoning
        result = detect_runaway_reasoning({"content": "short"}, 2.0)
        self.assertFalse(result)

    def test_detect_repetition(self):
        from core.llm_self_heal import detect_runaway_reasoning
        repeat_line = "The answer is clear and definitive. " * 400
        result = detect_runaway_reasoning({"content": repeat_line}, 30.0)
        self.assertTrue(result)


class TestDetectInvalidJSON(unittest.TestCase):
    """test_detect_invalid_json"""

    def test_detect_none_response(self):
        from core.llm_self_heal import detect_invalid_json
        self.assertFalse(detect_invalid_json(None))

    def test_detect_valid_json_is_ok(self):
        from core.llm_self_heal import detect_invalid_json
        result = detect_invalid_json({"content": '{"key": "value"}'})
        self.assertFalse(result)

    def test_detect_invalid_json(self):
        from core.llm_self_heal import detect_invalid_json
        result = detect_invalid_json({"content": '{"key": "value",}'})
        self.assertTrue(result)


class TestDetectToolError(unittest.TestCase):
    """test_detect_tool_error"""

    def test_detect_none_response(self):
        from core.llm_self_heal import detect_tool_error
        self.assertFalse(detect_tool_error(None))

    def test_detect_tool_error_marker(self):
        from core.llm_self_heal import detect_tool_error
        result = detect_tool_error({"content": "TOOL_ERROR: something went wrong"})
        self.assertTrue(result)

    def test_detect_tool_error_in_list(self):
        from core.llm_self_heal import detect_tool_error
        result = detect_tool_error({"content": "", "tool_calls": [{"tool": "math", "error": "failed"}]})
        self.assertTrue(result)


class TestDetectBadRouting(unittest.TestCase):
    """test_detect_bad_routing"""

    def test_detect_empty_routing(self):
        from core.llm_self_heal import detect_bad_routing
        result = detect_bad_routing({"content": ""})
        self.assertTrue(result)

    def test_detect_normal_routing(self):
        from core.llm_self_heal import detect_bad_routing
        result = detect_bad_routing({"content": "Here is the answer you asked for."})
        self.assertFalse(result)


class TestApplyRecovery(unittest.TestCase):
    """test_apply_recovery_strategy"""

    def test_recovery_for_runaway(self):
        from core.llm_self_heal import apply_recovery_strategy
        result = apply_recovery_strategy("runaway_latency")
        self.assertEqual(result["action"], "fallback_model")
        self.assertTrue(result["reset_kv"])

    def test_recovery_for_tool_error(self):
        from core.llm_self_heal import apply_recovery_strategy
        result = apply_recovery_strategy("tool_error")
        self.assertEqual(result["action"], "retry_free")


class TestContextReset(unittest.TestCase):
    """test_reset_context_on_failure"""

    def setUp(self):
        from core.llm_self_heal import reset_counters
        reset_counters()

    def test_no_reset_initially(self):
        from core.llm_self_heal import reset_context_on_failure
        self.assertFalse(reset_context_on_failure())

    def test_reset_after_anomalies(self):
        from core.llm_self_heal import _record, reset_context_on_failure
        _record("runaway_latency")
        _record("runaway_repetition")
        _record("invalid_json")
        self.assertTrue(reset_context_on_failure())
