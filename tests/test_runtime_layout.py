import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.runtime_layout import ensure_runtime_data_layout


class RuntimeLayoutTests(unittest.TestCase):
    def test_creates_dirs_and_mode_unix(self):
        if os.name == "nt":
            self.skipTest("chmod layout only asserted on Unix")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data_rt = root / "data" / "runtime"
            with patch.dict(
                os.environ,
                {
                    "PROJECT_ROOT": str(root),
                    "RUNTIME_ENSURE_DATA_LAYOUT": "true",
                    "RUNTIME_APPLY_DIR_MODE": "true",
                    "RUNTIME_DIR_MODE": "750",
                    "RESILIENCE_RUNTIME_DIR": str(data_rt),
                    "ERROR_ANALYSIS_DIR": str(root / "data"),
                },
                clear=False,
            ):
                ensure_runtime_data_layout()
            self.assertTrue(data_rt.is_dir())
            mode = stat.S_IMODE(data_rt.stat().st_mode)
            self.assertEqual(mode, 0o750)


if __name__ == "__main__":
    unittest.main()
