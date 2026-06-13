"""Tests for circuit breaker and cost budget guards."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from core.cost_controller import daily_cost_budget_usd, llm_daily_cost_blocked_reason
from core.resilience import CircuitBreaker


class TestCircuitBreaker(unittest.TestCase):
    def test_opens_after_threshold_failures(self) -> None:
        cb = CircuitBreaker(failure_threshold=3, window_sec=60.0, open_sec=5.0, name="test")
        for _ in range(3):
            cb.record_failure()
        self.assertFalse(cb.allow_request())

    def test_half_open_after_cooldown(self) -> None:
        cb = CircuitBreaker(failure_threshold=1, window_sec=60.0, open_sec=0.05, name="test")
        clock = {"t": 1000.0}

        def _fake_now() -> float:
            return clock["t"]

        with patch.object(cb, "_now", side_effect=_fake_now):
            cb.record_failure()
            self.assertFalse(cb.allow_request())
            clock["t"] += 0.1
            self.assertTrue(cb.allow_request())
            cb.record_success()
            self.assertTrue(cb.allow_request())


class TestDailyCostBudget(unittest.TestCase):
    def test_blocks_when_spent_exceeds_budget(self) -> None:
        os.environ["COST_DAILY_USD_BUDGET"] = "1.0"
        os.environ["COST_DAILY_USD_HARD_STOP"] = "true"
        with patch("core.cost_controller._today_cost_spent_usd", return_value=1.5):
            reason = llm_daily_cost_blocked_reason()
        self.assertIsNotNone(reason)
        self.assertIn("budget", reason.lower())

    def test_allows_under_budget(self) -> None:
        os.environ["COST_DAILY_USD_HARD_STOP"] = "true"
        with patch("core.cost_controller._today_cost_spent_usd", return_value=0.01):
            self.assertIsNone(llm_daily_cost_blocked_reason())

    def test_daily_cost_budget_default(self) -> None:
        os.environ.pop("COST_DAILY_USD_BUDGET", None)
        self.assertEqual(daily_cost_budget_usd(), 10.0)
