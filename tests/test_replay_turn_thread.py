"""CLI smoke for replay_turn_thread.py."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class TestReplayTurnThread(unittest.TestCase):
    def test_cli_runs_on_synthetic_jsonl(self) -> None:
        root = Path(__file__).resolve().parent.parent
        script = root / "scripts" / "replay_turn_thread.py"
        row = {
            "trace_id": "t-replay-1",
            "user_text": "какая погода",
            "short_circuit": "weather_direct",
            "lane": "FACT",
            "referent": "world",
            "recent_before": [],
        }
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            path = f.name
        empty = root / "data" / "runtime" / "_replay_test_empty.jsonl"
        empty.parent.mkdir(parents=True, exist_ok=True)
        empty.write_text("", encoding="utf-8")
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--ops-trace",
                    str(empty),
                    "--turns",
                    path,
                    "--limit",
                    "1",
                    "--json",
                ],
                cwd=str(root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr or proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload.get("replayed"), 1)
            self.assertEqual(payload["rows"][0].get("registry_lane"), "FACT")
        finally:
            Path(path).unlink(missing_ok=True)
            empty.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
