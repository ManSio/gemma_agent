"""turn_chain_audit route snapshot."""
from __future__ import annotations

import unittest

from core.turn_chain_audit import route_snapshot, trajectory_summary


class TestTurnChainAudit(unittest.TestCase):
    def test_route_snapshot_habr_preflight(self):
        url = "https://habr.com/ru/companies/x/articles/123/"
        snap = route_snapshot(url)
        self.assertEqual(snap.get("preflight_profile"), "summarization")

    def test_trajectory_summary_keys(self):
        snap = trajectory_summary("900000001", "https://habr.com/ru/articles/1/")
        self.assertIn("preflight_profile", snap)
        self.assertIn("profile", snap)
        self.assertIn("intent", snap)
        self.assertIn("module", snap)
        self.assertEqual(snap.get("preflight_profile"), "summarization")


if __name__ == "__main__":
    unittest.main()
