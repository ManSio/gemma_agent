"""Tests for TurnContract health gates."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "turn_contract_health.py"
_spec = importlib.util.spec_from_file_location("turn_contract_health", SCRIPT)
assert _spec and _spec.loader
_turn_contract_health = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_turn_contract_health)


class TestTurnContractHealthGate0(unittest.TestCase):
    def test_gate0_uses_recent_fingerprint_when_present(self) -> None:
        rows = [
            {"referent": "thread", "recent_fingerprint": "a"},
            {"referent": "agent", "recent_fingerprint": "a"},
            {"referent": "world", "recent_fingerprint": "b"},
        ]
        report = _turn_contract_health.gate0_referent_fingerprint(rows)
        self.assertTrue(report["ok"])
        self.assertEqual(report["referent_pct"], 100.0)
        self.assertEqual(report["recent_fingerprint_pct"], 100.0)
        self.assertEqual(report["fingerprint_pct"], 100.0)
        self.assertEqual(report["fp_fallback_rows"], 0)

    def test_gate0_accepts_fp_fallback_for_legacy_rows(self) -> None:
        rows = [
            {"referent": "thread", "fp": "legacy-a"},
            {"referent": "agent", "fp": "legacy-b"},
            {"referent": "world", "fp": "legacy-c"},
        ]
        report = _turn_contract_health.gate0_referent_fingerprint(rows)
        self.assertTrue(report["ok"])
        self.assertEqual(report["recent_fingerprint_pct"], 0.0)
        self.assertEqual(report["fingerprint_pct"], 100.0)
        self.assertEqual(report["fp_fallback_rows"], 3)

    def test_gate0_rejects_missing_referent_even_with_fp(self) -> None:
        rows = [
            {"fp": "legacy-a"},
            {"referent": "agent", "fp": "legacy-b"},
            {"referent": "world", "fp": "legacy-c"},
        ]
        report = _turn_contract_health.gate0_referent_fingerprint(rows)
        self.assertFalse(report["ok"])
        self.assertEqual(report["referent_pct"], 66.7)
        self.assertEqual(report["fingerprint_pct"], 100.0)


if __name__ == "__main__":
    unittest.main()
