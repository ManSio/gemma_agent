import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.system_housekeeping import run_housekeeping


class SystemHousekeepingTests(unittest.TestCase):
    def test_housekeeping_removes_expected_junk(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a" / "__pycache__").mkdir(parents=True, exist_ok=True)
            (root / "a" / "__pycache__" / "x.pyc").write_bytes(b"1")
            (root / ".pytest_cache").mkdir(parents=True, exist_ok=True)
            (root / ".pytest_cache" / "v").write_text("x", encoding="utf-8")
            (root / "tests").mkdir(parents=True, exist_ok=True)
            (root / "tests" / "_tmp_sample.jsonl").write_text("x", encoding="utf-8")
            with patch.dict(
                "os.environ",
                {
                    "HOUSEKEEPING_DELETE_TEST_TMP": "true",
                    "HOUSEKEEPING_TEST_TMP_MAX_AGE_HOURS": "0",
                    "HOUSEKEEPING_MAX_DELETE_PER_CYCLE": "100",
                    "HOUSEKEEPING_STORAGE_OPTIMIZE_ENABLED": "true",
                    "HOUSEKEEPING_JSONL_MAX_BYTES": "50",
                    "HOUSEKEEPING_JSONL_KEEP_LINES": "5",
                },
                clear=False,
            ):
                rt = root / "data" / "runtime"
                rt.mkdir(parents=True, exist_ok=True)
                with open(rt / "session_digest.jsonl", "w", encoding="utf-8") as f:
                    for i in range(20):
                        f.write(f'{{"i": {i}}}\n')
                rep = run_housekeeping(root_path=str(root), dry_run=False)
            self.assertTrue(rep.get("ok"))
            self.assertGreaterEqual(int(rep.get("removed_total") or 0), 3)
            self.assertFalse((root / "a" / "__pycache__").exists())
            self.assertFalse((root / ".pytest_cache").exists())
            self.assertFalse((root / "tests" / "_tmp_sample.jsonl").exists())
            st = rep.get("storage_optimization") or {}
            jc = st.get("jsonl_compaction") or []
            self.assertTrue(any(isinstance(x, dict) and x.get("path", "").endswith("session_digest.jsonl") for x in jc))
            self.assertIn(rep.get("profile"), {"safe", "balanced", "aggressive"})

    def test_housekeeping_respects_forced_profile(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.dict("os.environ", {"HOUSEKEEPING_PROFILE": "aggressive"}, clear=False):
                rep = run_housekeeping(root_path=str(root), dry_run=True)
            self.assertEqual(rep.get("profile"), "aggressive")


if __name__ == "__main__":
    unittest.main()
