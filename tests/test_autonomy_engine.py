"""
Regression tests for MEGA-TASK 8.0 — Autonomy Engine (v3.0.0).
Tests: reasoning cache hit, delta prompting, context stitching,
KV-reuse after topic change, tool batching, self-healing 2.0.
"""
import json
import os
import time
import unittest
from unittest.mock import patch

# ── Enable token_efficiency features ──
os.environ["TOKEN_EFFICIENCY_FORCE_CACHE"] = "1"


class TestReasoningCacheHit(unittest.TestCase):
    """test_reasoning_cache_hit"""

    def setUp(self):
        from core.llm_cache import llm_cache_clear
        llm_cache_clear()

    def test_cache_lookup_returns_stored_reasoning(self):
        from unittest.mock import patch
        from core.llm_cache import _REASONING_CACHE, _cache_key
        import time

        # Store directly in the internal cache dict
        key = _cache_key(
            model="test-model",
            system_prompt="You are a helpful assistant.",
            user_input="What is 2+2?",
            subject="math",
            memory_state="{}",
            tools_signature="[]",
        )
        _REASONING_CACHE[key] = {
            "ts": time.time(),
            "content": "The answer is 4.",
            "tool_calls": [],
            "reasoning_decision": {"mode": "just_answer", "intent": "general"},
        }

        # Now test lookup with cache enabled
        with patch("core.llm_cache.cache_enabled", return_value=True):
            from core.llm_cache import reasoning_cache_lookup
            cached = reasoning_cache_lookup(
                model="test-model",
                system_prompt="You are a helpful assistant.",
                user_input="What is 2+2?",
                subject="math",
                memory_state="{}",
                tools_signature="[]",
            )
            self.assertIsNotNone(cached)
            self.assertEqual(cached.get("content"), "The answer is 4.")
            self.assertEqual(cached.get("reasoning_decision"), {"mode": "just_answer", "intent": "general"})

    def test_cache_lookup_returns_none_on_miss(self):
        from core.llm_cache import reasoning_cache_lookup

        cached = reasoning_cache_lookup(
            model="test-model",
            system_prompt="system",
            user_input="unique query never cached",
            subject="",
            memory_state="",
            tools_signature="",
        )
        self.assertIsNone(cached)

    def test_cache_lookup_returns_none_on_disabled(self):
        from core.llm_cache import reasoning_cache_lookup
        from core.token_efficiency import cache_enabled

        if not cache_enabled():
            cached = reasoning_cache_lookup(
                model="test-model",
                system_prompt="system",
                user_input="any",
                subject="",
                memory_state="",
                tools_signature="",
            )
            self.assertIsNone(cached)


class TestDeltaPromptingSmallDiff(unittest.TestCase):
    """test_delta_prompting_small_diff"""

    def setUp(self):
        from core.context_snapshot import get_context_snapshot
        snap = get_context_snapshot()
        snap.clear()

    def test_small_change_returns_delta(self):
        from core.context_builder import ContextBuilder

        builder = ContextBuilder()
        builder._delta_enabled = True
        builder._delta_min_chars = 1000

        parts1 = {
            "system": "You are helpful.",
            "user": {"name": "Alice"},
            "conversation": {"msg_count": 1},
        }
        parts2 = {
            "system": "You are helpful.",
            "user": {"name": "Alice"},
            "conversation": {"msg_count": 2},
        }

        # Store first snapshot
        builder.delta_context(parts1)
        # Small diff should return delta
        is_delta, result = builder.delta_context(parts2)
        self.assertTrue(is_delta)
        self.assertTrue(result.get("__delta__"))


class TestDeltaPromptingLargeDiff(unittest.TestCase):
    """test_delta_prompting_large_diff"""

    def setUp(self):
        from core.context_snapshot import get_context_snapshot
        snap = get_context_snapshot()
        snap.clear()

    def test_large_change_returns_full(self):
        from core.context_builder import ContextBuilder

        builder = ContextBuilder()
        builder._delta_enabled = True
        builder._delta_min_chars = 5

        parts1 = {
            "system": "You are helpful.",
        }
        parts2 = {
            "system": "A completely different system prompt that is much much longer than the previous one.",
        }

        builder.delta_context(parts1)
        is_delta, result = builder.delta_context(parts2)
        self.assertFalse(is_delta)


class TestContextStitchingStaticHead(unittest.TestCase):
    """test_context_stitching_static_head"""

    def setUp(self):
        from core.context_builder import ContextBuilder
        self.builder = ContextBuilder()

    def test_static_head_is_reused(self):
        head1 = self.builder.build_stitched(
            system_prompt="You are helpful.",
            rules=["rule1", "rule2"],
            tools_declaration="tool_echo",
            policy={"max_tokens": 1000},
            persona={"name": "Bot"},
        )
        static_head_hash1 = head1["stitching"]["static_head_hash"]

        head2 = self.builder.build_stitched(
            system_prompt="You are helpful.",
            rules=["rule1", "rule2"],
            tools_declaration="tool_echo",
            policy={"max_tokens": 1000},
            persona={"name": "Bot"},
        )
        static_head_hash2 = head2["stitching"]["static_head_hash"]

        self.assertEqual(static_head_hash1, static_head_hash2)

    def test_static_head_changes_when_system_prompt_differs(self):
        head1 = self.builder.build_stitched(
            system_prompt="You are helpful.",
            rules=["rule1"],
            tools_declaration="",
            policy={},
            persona={},
        )
        hash1 = head1["stitching"]["static_head_hash"]

        head2 = self.builder.build_stitched(
            system_prompt="You are a different assistant.",
            rules=["rule1"],
            tools_declaration="",
            policy={},
            persona={},
        )
        hash2 = head2["stitching"]["static_head_hash"]

        self.assertNotEqual(hash1, hash2)

    def test_rolling_tail_limited(self):
        messages = [{"role": "user", "content": f"msg{i}"} for i in range(30)]
        ctx = self.builder.build_stitched(messages=messages)
        tail = ctx.get("rolling_tail", [])
        self.assertLessEqual(len(tail), 20)


class TestKVReuseAfterTopicChange(unittest.TestCase):
    """test_kv_reuse_after_topic_change"""

    def setUp(self):
        import core.brain.session_stickiness as ss
        ss._STATE.clear()

    def test_new_session_on_dialog_state_reset(self):
        from core.dialog_state import reset_dialog_state, get_kv_session_epoch
        import core.brain.session_stickiness as ss

        uid = "test_kv_topic_user"
        gid = "test_group"

        # Get initial session
        sid1, _ = ss.resolve_session(
            user_id=uid,
            group_id=gid,
            intent="general",
        )

        # Simulate dialog state reset (topic change)
        reset_dialog_state("topic_change", user_id=uid, group_id=gid)

        sid2, _ = ss.resolve_session(
            user_id=uid,
            group_id=gid,
            intent="general",
        )

        self.assertNotEqual(sid1, sid2)

    def test_force_session_reset_bumps_epoch(self):
        import core.brain.session_stickiness as ss

        uid = "test_force_user"
        gid = None

        sid1, _ = ss.resolve_session(user_id=uid, group_id=gid, intent="general")
        sid2 = ss.force_session_reset(user_id=uid, group_id=gid, reason="test")
        self.assertNotEqual(sid1, sid2)


class TestToolBatching(unittest.TestCase):
    """test_tool_batching"""

    def test_check_tool_dependencies_satisfied(self):
        from core.planning_layer import check_tool_dependencies

        tool_calls = [
            {"tool": "vision_ocr", "args": {}},
            {"tool": "document_reader", "args": {}},
        ]
        self.assertTrue(check_tool_dependencies(tool_calls))

    def test_check_tool_dependencies_missing(self):
        from core.planning_layer import check_tool_dependencies

        tool_calls = [
            {"tool": "document_reader", "args": {}},
        ]
        # document_reader depends on vision_ocr/corpus_search/download
        self.assertFalse(check_tool_dependencies(tool_calls))

    def test_resolve_tool_order_deps_first(self):
        from core.planning_layer import resolve_tool_order

        tool_calls = [
            {"tool": "document_reader", "args": {}},
            {"tool": "vision_ocr", "args": {}},
        ]
        ordered = resolve_tool_order(tool_calls)
        self.assertEqual(ordered[0]["tool"], "vision_ocr")

    def test_batch_validation(self):
        from core.tool_router import check_tool_calls_batch

        batch = [
            {"tool": "download", "args": {"url": "https://example.com"}},
            {"tool": "SelfProgramming.analyze", "args": {}},
        ]
        result = check_tool_calls_batch(tool_calls=batch, allow_self_programming=False)
        self.assertFalse(result["all_allowed"])
        self.assertEqual(result["blocked_count"], 1)

    def test_patch_queue_and_confirm(self):
        from core.tool_router import queue_patch_for_confirmation, get_pending_patch, confirm_patch, reject_patch

        pid = queue_patch_for_confirmation(
            patch_id="test-patch-1",
            issue={"type": "module_failed", "module": "test_mod"},
            patch={"type": "module_repair"},
            diff="+ module.load()",
            tool_name="SelfProgramming.repair",
        )

        pending = get_pending_patch(pid)
        self.assertIsNotNone(pending)
        self.assertEqual(pending["diff"], "+ module.load()")

        self.assertTrue(confirm_patch(pid))
        self.assertIsNone(get_pending_patch(pid))

        pid2 = queue_patch_for_confirmation(
            patch_id="test-patch-2",
            issue={"type": "test"},
            patch={"action": "fix"},
            diff="- old line",
            tool_name="test_tool",
        )
        self.assertTrue(reject_patch(pid2))
        self.assertIsNone(get_pending_patch(pid2))


class TestSelfHealingOnToolFailure(unittest.TestCase):
    """test_self_healing_on_tool_failure"""

    def setUp(self):
        from core.self_healing import reset_anomalies, reset_error_counters
        reset_anomalies()
        reset_error_counters()

    def test_record_tool_failure_increments_counter(self):
        from core.self_healing import record_tool_failure, get_anomalies

        record_tool_failure("download", "connection refused")
        anomalies = get_anomalies()
        self.assertEqual(anomalies["counts"]["tool_failures"], 1)

    def test_record_reasoning_timeout(self):
        from core.self_healing import record_reasoning_timeout, get_anomalies

        record_reasoning_timeout()
        anomalies = get_anomalies()
        self.assertEqual(anomalies["counts"]["reasoning_timeouts"], 1)

    def test_record_collapse_overflow(self):
        from core.self_healing import record_collapse_overflow, get_anomalies

        record_collapse_overflow()
        anomalies = get_anomalies()
        self.assertEqual(anomalies["counts"]["collapse_overflows"], 1)

    def test_record_kv_drift(self):
        from core.self_healing import record_kv_drift, get_anomalies

        record_kv_drift()
        anomalies = get_anomalies()
        self.assertEqual(anomalies["counts"]["kv_drift"], 1)

    def test_log_tool_error_triggers_auto_reset(self):
        from core.self_healing import log_tool_error, should_auto_reset, reset_error_counters

        reset_error_counters()
        log_tool_error("tool_a", 0, "error 1")
        log_tool_error("tool_a", 1, "error 2")
        log_tool_error("tool_a", 2, "error 3")

        self.assertTrue(should_auto_reset())
        self.assertFalse(should_auto_reset())  # Counter cleared after reset


if __name__ == "__main__":
    unittest.main()
