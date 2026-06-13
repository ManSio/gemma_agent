"""Tests for config/token_efficiency.yml accessors."""

import unittest

from core.token_efficiency import (
    budget_enabled,
    budget_hard_limit_tokens,
    compactor_enabled,
    compactor_threshold,
)


class TokenEfficiencyConfigTests(unittest.TestCase):
    def test_budget_and_compactor_from_yaml(self):
        """Repo config: budget 15K, compactor enabled (YAML fix 2026-06)."""
        self.assertTrue(budget_enabled())
        self.assertGreaterEqual(budget_hard_limit_tokens(), 15000)
        self.assertTrue(compactor_enabled())
        self.assertGreater(compactor_threshold(), 0.0)


if __name__ == "__main__":
    unittest.main()
