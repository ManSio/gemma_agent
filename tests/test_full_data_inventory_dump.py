"""Скрипт полного инвентаря данных не падает на пустом/минимальном дереве."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def test_full_data_inventory_dump_runs_fast(tmp_path):
    (tmp_path / "data" / "runtime").mkdir(parents=True)
    (tmp_path / "data" / "runtime" / "turns.jsonl").write_text(
        '{"ts":"2026-01-01T00:00:00+00:00","intent":"general"}\n',
        encoding="utf-8",
    )
    r = subprocess.run(
        [
            sys.executable,
            str(_REPO / "scripts" / "full_data_inventory_dump.py"),
            "--root",
            str(tmp_path),
            "--fast",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_path),
    )
    assert r.returncode == 0, r.stderr
    assert "turns.jsonl" in r.stdout
    assert "full_data_inventory_dump" in r.stdout
