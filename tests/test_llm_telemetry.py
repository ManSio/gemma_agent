import os
import unittest
from unittest.mock import patch

from core.llm_telemetry import normalize_llm_usage_row, record_openrouter_completion, usage_summary


class LlmTelemetryTests(unittest.TestCase):
    def test_usage_summary_cost_and_reasoning(self):
        u = {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "cost": 0.00042,
            "completion_tokens_details": {"reasoning_tokens": 3},
        }
        s = usage_summary(u)
        self.assertEqual(s["total_tokens"], 15)
        self.assertAlmostEqual(s["cost"], 0.00042)
        self.assertEqual(s["reasoning_tokens"], 3)

    def test_usage_summary_prompt_cache_details(self):
        u = {
            "prompt_tokens": 1000,
            "completion_tokens": 50,
            "total_tokens": 1050,
            "prompt_tokens_details": {"cached_tokens": 900, "cache_write_tokens": 100},
        }
        s = usage_summary(u)
        self.assertEqual(s["cached_prompt_tokens"], 900)
        self.assertEqual(s["cache_write_tokens"], 100)

    @patch.dict(
        os.environ,
        {"GEMMA_LLM_AUDIT_LOG": "", "GEMMA_LLM_USAGE_PERSIST": "false"},
        clear=False,
    )
    def test_record_increments_monitor_on_ok(self):
        from core.monitoring import MONITOR

        before = dict(MONITOR.counters)
        record_openrouter_completion(
            ok=True,
            requested_model="m1",
            upstream_model="m2",
            latency_ms=12.5,
            usage={"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5, "cost": 0.0},
            content_chars=4,
        )
        self.assertGreaterEqual(MONITOR.counters.get("openrouter_completion_ok_total", 0), before.get("openrouter_completion_ok_total", 0) + 1)
        self.assertGreaterEqual(MONITOR.counters.get("openrouter_prompt_tokens_total", 0), before.get("openrouter_prompt_tokens_total", 0) + 2)

    def test_normalize_llm_usage_row_fills_empty_tag(self):
        row = normalize_llm_usage_row({"prompt_tokens": 1})
        self.assertEqual(row["telemetry_tag"], "openrouter_chat")
        self.assertTrue(row.get("telemetry_kind"))


if __name__ == "__main__":
    unittest.main()
