import unittest
import zipfile
from io import BytesIO

from core.boot_timeline import boot_timeline_snapshot, mark_boot
from core.diagnostic_bundle import diagnostic_bundle_zip_bytes


class TestBootTimeline(unittest.TestCase):
    def test_marks_order(self):
        mark_boot("test_mark_a")
        mark_boot("test_mark_b")
        snap = boot_timeline_snapshot()
        self.assertGreaterEqual(len(snap["marks"]), 2)
        names = [m["name"] for m in snap["marks"]]
        self.assertIn("test_mark_a", names)
        idx_a = names.index("test_mark_a")
        idx_b = names.index("test_mark_b")
        self.assertLess(idx_a, idx_b)
        self.assertGreater(snap["marks"][idx_b]["delta_ms"], snap["marks"][idx_a]["delta_ms"])


class TestDiagnosticZip(unittest.TestCase):
    def test_zip_contains_json_and_readme(self):
        zbytes = diagnostic_bundle_zip_bytes({"ok": True, "x": 1})
        with zipfile.ZipFile(BytesIO(zbytes), "r") as zf:
            names = zf.namelist()
            self.assertIn("bundle.json", names)
            self.assertTrue(any(n.endswith(".txt") for n in names))
            raw = zf.read("bundle.json").decode("utf-8")
            self.assertIn('"ok": true', raw)
