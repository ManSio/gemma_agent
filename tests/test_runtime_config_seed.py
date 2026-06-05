import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from core.runtime_config_seed import (
    format_runtime_seed_report_ru,
    seed_runtime_config_from_examples,
)


class RuntimeConfigSeedTests(unittest.TestCase):
    def test_creates_directive_when_missing(self):
        root = Path(__file__).resolve().parent.parent
        with TemporaryDirectory() as td:
            rt = Path(td) / "runtime"
            rt.mkdir(parents=True)
            with patch.dict(
                os.environ,
                {
                    "PROJECT_ROOT": str(root),
                    "RESILIENCE_RUNTIME_DIR": str(rt),
                },
                clear=False,
            ):
                rep = seed_runtime_config_from_examples(force_directive=False, force_operator_rules=False)
            dest = rt / "system_directive_addon.txt"
            self.assertTrue(dest.is_file(), msg=repr(rep))
            self.assertGreater(dest.stat().st_size, 50)
            self.assertIn("directive", rep)
            self.assertIn("written", str(rep.get("directive") or ""))

    def test_format_report_ru_readable(self):
        html_out = format_runtime_seed_report_ru(
            {"directive": "written:system_directive_addon", "operator_rules": None}
        )
        self.assertIn("записан", html_out.lower())
        self.assertIn("не изменён", html_out.lower())


if __name__ == "__main__":
    unittest.main()
