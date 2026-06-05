import unittest

from core.observability import Observability


class ObservabilityTests(unittest.TestCase):
    def test_trace_lifecycle_and_snapshot(self):
        obs = Observability()
        tr = obs.new_trace()
        obs.stage(tr.trace_id, "planned")
        elapsed = obs.finish(tr.trace_id, label="pipeline")
        self.assertIsNotNone(elapsed)
        snap = obs.snapshot()
        self.assertIn("counters", snap)
        self.assertIn("latency_p95_ms", snap)
        self.assertEqual(snap["active_traces"], 0)


if __name__ == "__main__":
    unittest.main()
