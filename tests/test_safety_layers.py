"""
Regression tests for safety layers (safety.yml feature flags).
Tests: context isolation, subject decay, memory recall guard,
tool call validation, selfprogramming block, reasoning reset,
KV session reset, collapse overflow reset, fast-path safety, timeout protection.
"""

import os
import time
import unittest
from unittest.mock import patch

# ── Patch safety config to enable all features ──
os.environ["SAFETY_CONFIG_FORCE_ALL"] = "1"

from core.safety_config import (
    tool_guard_enabled,
    context_reset_enabled,
    reasoning_reset_enabled,
    subject_decay_enabled,
    memory_recall_guard_enabled,
    kv_session_reset_enabled,
    fast_path_safety_enabled,
    max_reasoning_ms,
    noise_sequence_limit,
)


class TestSafetyConfig(unittest.TestCase):
    def test_safety_flags_exist(self):
        self.assertIsInstance(tool_guard_enabled(), bool)
        self.assertIsInstance(context_reset_enabled(), bool)
        self.assertIsInstance(reasoning_reset_enabled(), bool)
        self.assertIsInstance(subject_decay_enabled(), bool)
        self.assertIsInstance(memory_recall_guard_enabled(), bool)
        self.assertIsInstance(kv_session_reset_enabled(), bool)
        self.assertIsInstance(fast_path_safety_enabled(), bool)
        self.assertIsInstance(max_reasoning_ms(), int)
        self.assertIsInstance(noise_sequence_limit(), int)

    def test_max_reasoning_ms_is_positive(self):
        self.assertGreater(max_reasoning_ms(), 0)

    def test_noise_sequence_limit_is_positive(self):
        self.assertGreater(noise_sequence_limit(), 2)


class TestContextIsolationAfterNoise(unittest.TestCase):
    """test_context_isolation_after_noise_sequence_20"""

    def setUp(self):
        from core.dialog_state import (
            reset_dialog_state,
            should_trigger_reset,
            ensure_state,
        )
        self.uid = "test_noise_user"
        self.gid = None
        # Start with a clean state
        state = ensure_state(self.uid, self.gid)
        state["noise_count"] = 0

    def test_noise_sequence_triggers_reset(self):
        from core.dialog_state import should_trigger_reset
        limit = noise_sequence_limit()
        triggered = False
        for i in range(limit + 5):
            reason = should_trigger_reset(
                user_id=self.uid,
                group_id=self.gid,
                has_task=False,
            )
            if reason and "noise_sequence" in (reason or ""):
                triggered = True
                break
        self.assertTrue(triggered, f"Noise sequence of {limit}+ should trigger reset")

    def test_task_resets_noise_count(self):
        from core.dialog_state import ensure_state, should_trigger_reset
        # Send some noise
        for i in range(3):
            should_trigger_reset(user_id=self.uid, group_id=self.gid, has_task=False)
        # Send a task
        should_trigger_reset(user_id=self.uid, group_id=self.gid, has_task=True)
        state = ensure_state(self.uid, self.gid)
        self.assertEqual(state.get("noise_count", -1), 0)

    def test_reset_dialog_state_clears_all(self):
        from core.dialog_state import reset_dialog_state, ensure_state
        state = ensure_state(self.uid, self.gid)
        state["subject_context"] = "test_subject"
        state["memory_recall_allowed"] = False
        state["active_document"] = "test_doc"

        reset_dialog_state("test", user_id=self.uid, group_id=self.gid)

        state2 = ensure_state(self.uid, self.gid)
        self.assertIsNone(state2.get("subject_context"))
        self.assertTrue(state2.get("memory_recall_allowed"))
        self.assertIsNone(state2.get("active_document"))
        self.assertEqual(state2.get("noise_count"), 0)


class TestSubjectContextDecay(unittest.TestCase):
    """test_subject_context_decay"""

    def setUp(self):
        self.uid = "test_decay_user"
        self.gid = None

    def test_decay_after_5_turns_without_reference(self):
        from core.subject_context import (
            record_turn,
            should_clear,
            get_turns_without_reference,
        )
        # Send 5 turns without reference
        for i in range(5):
            record_turn(user_id=self.uid, group_id=self.gid, has_reference=False)
        self.assertTrue(should_clear(user_id=self.uid, group_id=self.gid))
        self.assertGreaterEqual(get_turns_without_reference(self.uid, self.gid), 5)

    def test_reference_resets_counter(self):
        from core.subject_context import (
            record_turn,
            should_clear,
        )
        # 4 turns without reference
        for i in range(4):
            record_turn(user_id=self.uid, group_id=self.gid, has_reference=False)
        # 1 turn with reference
        record_turn(user_id=self.uid, group_id=self.gid, has_reference=True)
        self.assertFalse(should_clear(user_id=self.uid, group_id=self.gid))

    def test_clear_subject_context(self):
        from core.subject_context import (
            clear_subject_context,
            get_turns_without_reference,
        )
        clear_subject_context(user_id=self.uid, group_id=self.gid)
        self.assertEqual(get_turns_without_reference(self.uid, self.gid), 0)


class TestMemoryRecallGuard(unittest.TestCase):
    """test_memory_recall_guard"""

    def test_explicit_recall_triggers_allowed(self):
        from core.memory_recall import memory_recall_allowed
        for trigger in ["напомни", "что было раньше", "вспомни", "помнишь"]:
            self.assertTrue(
                memory_recall_allowed(user_text=trigger),
                f"'{trigger}' should allow memory recall",
            )

    def test_non_recall_message_blocked(self):
        from core.memory_recall import memory_recall_allowed
        self.assertFalse(
            memory_recall_allowed(user_text="Привет, как дела?"),
            "Generic message should not allow memory recall",
        )

    def test_old_object_references_allowed(self):
        from core.memory_recall import memory_recall_allowed
        self.assertTrue(
            memory_recall_allowed(
                user_text="что там",
                recent_messages=["помнишь тот документ", "продолжи"],
            ),
        )

    def test_no_recent_history_blocked(self):
        from core.memory_recall import memory_recall_allowed
        self.assertFalse(
            memory_recall_allowed(
                user_text="расскажи что-нибудь",
                recent_messages=["Привет", "Как дела", "Хорошо"],
            ),
        )


class TestToolCallValidation(unittest.TestCase):
    """test_tool_call_missing_args"""

    def test_missing_args_blocked(self):
        from core.tool_router import check_tool_call
        result = check_tool_call(
            tool_name="download",
            args={},
            allow_self_programming=False,
            is_fast_path=False,
            has_explicit_tool_request=True,
        )
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "missing_required_args")

    def test_valid_args_allowed(self):
        from core.tool_router import check_tool_call
        result = check_tool_call(
            tool_name="download",
            args={"url": "https://example.com/file.zip"},
            allow_self_programming=False,
            is_fast_path=False,
            has_explicit_tool_request=True,
        )
        self.assertTrue(result.allowed)

    def test_selfprogramming_blocked(self):
        from core.tool_router import check_tool_call
        result = check_tool_call(
            tool_name="SelfProgramming.analyze",
            args={},
            allow_self_programming=False,
        )
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "self_programming_blocked")

    def test_selfprogramming_allowed_when_enabled(self):
        from core.tool_router import check_tool_call
        result = check_tool_call(
            tool_name="SelfProgramming.analyze",
            args={},
            allow_self_programming=True,
        )
        # SelfProgramming has no required args, so it should pass
        self.assertTrue(result.allowed)

    def test_fast_path_no_explicit_request_blocked(self):
        from core.tool_router import check_tool_call
        result = check_tool_call(
            tool_name="download",
            args={"url": "https://example.com/file.zip"},
            is_fast_path=True,
            has_explicit_tool_request=False,
        )
        self.assertFalse(result.allowed)


class TestReasoningReset(unittest.TestCase):
    """test_reasoning_reset_on_topic_change"""

    def test_reset_chain_clears_state(self):
        from core.reasoning_layer import (
            reset_chain,
            get_reasoning_chain,
            start_reasoning_timer,
        )
        reset_chain("test")
        self.assertEqual(len(get_reasoning_chain()), 0)

    def test_reasoning_time_check(self):
        from core.reasoning_layer import (
            start_reasoning_timer,
            reasoning_exceeded_time,
        )
        start_reasoning_timer()
        # Should not exceed time immediately
        self.assertFalse(reasoning_exceeded_time())

    def test_abort_reasoning_returns_fallback(self):
        from core.reasoning_layer import abort_reasoning
        result = abort_reasoning()
        self.assertEqual(result["mode"], "just_answer")
        self.assertFalse(result["should_call_tool"])
        self.assertEqual(result["reason"], "reasoning_timeout")


class TestKVSessionReset(unittest.TestCase):
    """test_kv_session_reset_on_reset"""

    def setUp(self):
        self.uid = "test_kv_user"
        self.gid = None

    def test_reset_increments_epoch(self):
        from core.dialog_state import (
            reset_dialog_state,
            get_kv_session_epoch,
        )
        epoch0 = get_kv_session_epoch(self.uid, self.gid)
        reset_dialog_state("test_reset", user_id=self.uid, group_id=self.gid)
        epoch1 = get_kv_session_epoch(self.uid, self.gid)
        self.assertGreater(epoch1, epoch0)


class TestCollapseOverflowReset(unittest.TestCase):
    """test_collapse_overflow_reset"""

    def test_should_trigger_on_collapse_overflow(self):
        from core.dialog_state import should_trigger_reset
        reason = should_trigger_reset(
            user_id="test_collapse_user",
            collapse_overflow=True,
        )
        self.assertEqual(reason, "collapse_overflow")

    def test_should_trigger_on_tool_call_failure(self):
        from core.dialog_state import should_trigger_reset
        uid = "test_toolfail_user"
        # First two failures shouldn't trigger
        r1 = should_trigger_reset(user_id=uid, tool_call_failure=True)
        r2 = should_trigger_reset(user_id=uid, tool_call_failure=True)
        r3 = should_trigger_reset(user_id=uid, tool_call_failure=True)
        self.assertEqual(r3, "tool_call_failure")


class TestFastPathSafety(unittest.TestCase):
    """test_fast_path_safety"""

    def test_fast_path_requires_explicit_request_for_tools(self):
        from core.tool_router import check_tool_call
        # Fast-path tool calls without explicit request should be blocked
        result = check_tool_call(
            tool_name="url_check",
            args={"url": "https://example.com"},
            is_fast_path=True,
            has_explicit_tool_request=False,
        )
        self.assertFalse(result.allowed)

    def test_digital_twin_allowed_in_fast_path(self):
        from core.tool_router import check_tool_call
        result = check_tool_call(
            tool_name="digital_twin",
            args={},
            is_fast_path=True,
            has_explicit_tool_request=False,
        )
        # digital_twin is in _FAST_PATH_ALWAYS_ALLOWED
        self.assertTrue(result.allowed)


class TestTimeoutProtection(unittest.TestCase):
    """test_timeout_protection"""

    def test_reasoning_timeout_levels(self):
        from core.safety_config import max_reasoning_ms
        self.assertGreater(max_reasoning_ms(), 0)

    def test_abort_reasoning_fallback_short(self):
        from core.reasoning_layer import abort_reasoning
        result = abort_reasoning()
        self.assertEqual(result["mode"], "just_answer")
        self.assertEqual(result["depth"], "shallow")


if __name__ == "__main__":
    unittest.main()
