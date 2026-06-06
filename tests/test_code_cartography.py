import os
import unittest
from pathlib import Path

from core import code_cartography as cc


class CodeCartographyTests(unittest.TestCase):
    def test_scan_and_diff_in_temp_repo(self):
        import tempfile

        try:
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                (root / "core").mkdir(parents=True)
                (root / "modules").mkdir(parents=True)
                (root / "data" / "runtime").mkdir(parents=True)
                f1 = root / "core" / "a.py"
                f1.write_text("x = 1\n", encoding="utf-8")
                f2 = root / "modules" / "b.py"
                f2.write_text("y = 2\n", encoding="utf-8")

                os.environ["CODE_CARTO_ROOT"] = str(root)
                os.environ["CODE_CARTO_LEDGER_PATH"] = str(root / "data" / "runtime" / "code_ledger.json")
                os.environ["CODE_CARTO_HISTORY_PATH"] = str(root / "data" / "runtime" / "code_history.jsonl")

                r1 = cc.scan_and_maybe_record(persist=True, root=root)
                self.assertTrue(r1.ledger_written)
                self.assertEqual(r1.snapshot["file_count"], 2)

                f1.write_text("x = 2\n", encoding="utf-8")
                r2 = cc.scan_and_maybe_record(persist=True, root=root)
                sl = r2.snapshot["since_last_ledger"]
                self.assertIn("core/a.py", sl.get("modified") or [])

                hist = cc.tail_history(Path(os.environ["CODE_CARTO_HISTORY_PATH"]), 5)
                self.assertGreaterEqual(len(hist), 1)
                self.assertEqual(hist[-1].get("event"), "scan")
        finally:
            for k in ("CODE_CARTO_ROOT", "CODE_CARTO_LEDGER_PATH", "CODE_CARTO_HISTORY_PATH"):
                os.environ.pop(k, None)

    def test_baseline_drift(self):
        import tempfile

        try:
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                (root / "core").mkdir(parents=True)
                (root / "data" / "runtime").mkdir(parents=True)
                p = root / "core" / "x.py"
                p.write_text("v=1\n", encoding="utf-8")
                os.environ["CODE_CARTO_ROOT"] = str(root)
                os.environ["CODE_CARTO_DIRS"] = "core"
                files = cc.scan_python_sources(root)
                bp = root / "data" / "runtime" / "base.json"
                cc.save_baseline(files, root=root, dest=bp)
                # Другой размер, иначе при равном mtime (Windows) только size/mtime не увидят правку.
                p.write_text("v=12\n", encoding="utf-8")
                files2 = cc.scan_python_sources(root)
                d = cc.compare_to_baseline(files2, bp)
                self.assertTrue(d.get("baseline_present"))
                self.assertIn("core/x.py", d.get("drift_modified") or [])
        finally:
            for k in ("CODE_CARTO_ROOT", "CODE_CARTO_DIRS"):
                os.environ.pop(k, None)


if __name__ == "__main__":
    unittest.main()
