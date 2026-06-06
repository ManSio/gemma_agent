import tempfile
import unittest
from pathlib import Path

from core.performance_probe import collect_performance_snapshot, run_storage_io_probe


class StorageProbeTests(unittest.TestCase):
    def test_io_probe_small_file(self):
        with tempfile.TemporaryDirectory() as d:
            r = run_storage_io_probe(base_dir=Path(d), size_bytes=65536)
            self.assertTrue(r.get("ok"))
            self.assertIn("write_fsync_ms", r)
            self.assertIn("read_ms", r)
            self.assertGreater(r["write_fsync_ms"], 0)


class CollectSnapshotTests(unittest.TestCase):
    def test_collect_returns_structure(self):
        s = collect_performance_snapshot()
        self.assertIn("host_resources", s)
        self.assertIn("storage_io_probe", s)
        self.assertIn("hints", s)
