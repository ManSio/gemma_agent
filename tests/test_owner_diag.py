"""owner_diag — сводка для /diag и gemma_status."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.owner_diag import collect_owner_diag, format_owner_diag_html

_ENV_OK = {
    "BRAIN_OPERATOR_CORRECTIONS_IN_HINT": "true",
    "MCE_ENABLED": "false",
    "MCE_AUTO_APPLY": "false",
    "GOAL_RUNNER_AUTO_START": "false",
    "ROUTER_PASSIVE_ENABLED": "false",
    "TURN_QUALITY_LOOP_ENABLED": "false",
    "TURN_QUALITY_AUTO_PENDING_CORRECTION": "false",
}


class TestOwnerDiag(unittest.TestCase):
    def test_collect_flags_ok_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, _ENV_OK, clear=False):
            root = Path(td)
            runtime = root / "data" / "runtime"
            runtime.mkdir(parents=True)
            (runtime / "boot_state.json").write_text(
                json.dumps({"last_start_utc": "2026-05-31T12:00:00+00:00", "restart_detected": False}),
                encoding="utf-8",
            )
            st = collect_owner_diag(root)
            self.assertIn("checks", st)
            self.assertTrue(st.get("ok"), st.get("problems"))

    def test_html_contains_diag_title(self) -> None:
        html = format_owner_diag_html({"ok": True, "ts_utc": "2026-05-31T12:00:00+00:00", "boot": {}, "autopilot": {"mode": False}, "checks": [], "files": {}, "turns_tail": {}, "problems": []})
        self.assertIn("Диагностика Gemma", html)


if __name__ == "__main__":
    unittest.main()
