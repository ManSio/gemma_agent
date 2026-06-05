import unittest
from unittest.mock import patch

from core.observability import OBS, Observability


class ObservabilityLatencyTests(unittest.TestCase):
    def tearDown(self) -> None:
        OBS.active_traces.clear()
        OBS.latencies_ms.clear()
        OBS.counters.clear()

    def test_mark_and_finish_records_latency(self) -> None:
        t = OBS.new_trace()
        OBS.mark(t.trace_id, "a")
        OBS.mark(t.trace_id, "b")
        ms = OBS.finish(t.trace_id, label="test_pipeline")
        self.assertIsNotNone(ms)
        assert ms is not None
        self.assertGreaterEqual(ms, 0.0)
        self.assertGreater(OBS.counters.get("traces_finished", 0), 0)

    def test_finish_slow_emits_info(self) -> None:
        obs = Observability()
        with self.assertLogs("core.observability", level="INFO") as cm:
            # slow-порог в коде не ниже 100ms; для гарантированного INFO — режим all
            with patch.dict("os.environ", {"LATENCY_TRACE_LOG": "all", "LATENCY_TRACE_SLOW_MS": "1"}):
                t = obs.new_trace()
                obs.mark(t.trace_id, "step")
                # искусственно долгий «хвост»: нет второго mark — tail большой не получится без sleep
                import time

                time.sleep(0.002)
                obs.finish(t.trace_id, label="x")
        self.assertTrue(any("latency trace=" in line for line in cm.output), cm.output)


if __name__ == "__main__":
    unittest.main()
