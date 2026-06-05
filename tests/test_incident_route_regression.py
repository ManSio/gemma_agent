"""Регрессия инцидента 2026-05-21: кейсы из build_test_corpus (route_only)."""
from __future__ import annotations

import unittest

from core.agent_test_validators import validate_reply
from scripts.build_test_corpus import _regression_cases


class TestIncidentRouteRegression(unittest.TestCase):
    def test_route_only_incident_cases_pass(self):
        cases = [
            c
            for c in _regression_cases()
            if c.get("route_only") and "incident_20260521" in (c.get("tags") or [])
        ]
        self.assertEqual(len(cases), 3, cases)
        for case in cases:
            errs = validate_reply("", str(case.get("text") or ""), case)
            self.assertEqual(errs, [], f"{case.get('id')}: {errs}")


if __name__ == "__main__":
    unittest.main()
