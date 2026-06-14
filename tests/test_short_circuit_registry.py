"""Tests for short-circuit registry."""
from __future__ import annotations

import unittest

from core.short_circuit_registry import (
    all_registered_shortcuts,
    lookup_short_circuit,
    record_short_circuit_use,
    register_short_circuit,
)


class TestShortCircuitRegistry(unittest.TestCase):
    def test_weather_registered(self) -> None:
        ent = lookup_short_circuit("weather_direct")
        self.assertIsNotNone(ent)
        self.assertEqual(ent.get("lane"), "FACT")

    def test_record_patches_meta(self) -> None:
        meta = {"turn_contract": {}, "trace_id": "abc"}
        record_short_circuit_use("news_direct", input_meta=meta, trace_id="abc")
        tc = meta.get("turn_contract")
        self.assertEqual(tc.get("short_circuit"), "news_direct")
        self.assertEqual(tc.get("lane"), "FACT")

    def test_runtime_register(self) -> None:
        register_short_circuit("custom_sc", {"lane": "DEEP", "intent": "test"})
        ent = lookup_short_circuit("custom_sc")
        self.assertEqual(ent.get("lane"), "DEEP")
        self.assertIn("custom_sc", all_registered_shortcuts())


if __name__ == "__main__":
    unittest.main()
