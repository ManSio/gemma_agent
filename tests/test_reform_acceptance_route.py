"""Регрессия brain-centric реформы: plan bypass выкл, gate/preflight."""
from __future__ import annotations

import os
import unittest

from core.agent_test_validators import validate_reply
from scripts.build_test_corpus import _reform_acceptance_cases


class TestReformAcceptanceRoute(unittest.TestCase):
    def setUp(self) -> None:
        self._env = dict(os.environ)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)

    def test_reform_route_cases_pass(self) -> None:
        os.environ["BRAIN_OWN_TURN_ENABLED"] = "true"
        for k in ("NEWS", "WEATHER", "GEO_NEARBY", "AFFIRMATIVE_SEARCH"):
            os.environ[f"BRAIN_OWN_TURN_ALLOW_{k}"] = "false"
        cases = _reform_acceptance_cases()
        self.assertGreaterEqual(len(cases), 6, cases)
        for case in cases:
            errs = validate_reply("", str(case.get("text") or ""), case)
            self.assertEqual(errs, [], f"{case.get('id')}: {errs}")


if __name__ == "__main__":
    unittest.main()
