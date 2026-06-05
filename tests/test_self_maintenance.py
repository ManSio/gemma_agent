import unittest
from unittest.mock import patch

from core.self_maintenance import SelfMaintenanceCycles


class SelfMaintenanceTests(unittest.TestCase):
    @patch("core.usage_learning.seconds_since_activity", return_value=9999.0)
    def test_cycle_interval_gate(self, mock_activity):
        m = SelfMaintenanceCycles()
        first = m.maybe_run(interval_sec=60.0)
        self.assertFalse(first["ran"])

        m.last_run_ts = 0.0
        second = m.maybe_run(interval_sec=60.0)
        self.assertTrue(second["ran"])

        third = m.maybe_run(interval_sec=60.0)
        self.assertFalse(third["ran"])


if __name__ == "__main__":
    unittest.main()
