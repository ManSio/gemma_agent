"""PolicyEngine call_history must not grow unbounded keys."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from core.policy_engine import PolicyEngine, Role


class TestPolicyEngineCallHistory(unittest.TestCase):
    def test_empty_keys_removed_after_prune(self) -> None:
        pe = PolicyEngine()
        key = f"{Role.USER}_old_module"
        pe.call_history[key] = [datetime.now() - timedelta(minutes=20)]
        pe._prune_call_history()
        self.assertNotIn(key, pe.call_history)

    def test_active_keys_retained(self) -> None:
        pe = PolicyEngine()
        pe._record_call(Role.USER, "chat")
        self.assertIn(f"{Role.USER}_chat", pe.call_history)
        self.assertEqual(len(pe.call_history[f"{Role.USER}_chat"]), 1)

    def test_rate_limit_check_also_prunes(self) -> None:
        pe = PolicyEngine()
        stale = f"{Role.ADMIN}_stale"
        pe.call_history[stale] = [datetime.now() - timedelta(hours=2)]
        pe.call_history[f"{Role.USER}_live"] = [datetime.now()]
        with patch.object(pe, "policies", pe.policies):
            pe._check_rate_limit(Role.USER, "live")
        self.assertNotIn(stale, pe.call_history)
        self.assertIn(f"{Role.USER}_live", pe.call_history)
