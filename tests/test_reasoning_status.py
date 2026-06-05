from __future__ import annotations

import os
import tempfile

from core.reasoning_status import load_reasoning_bench_snapshot, save_reasoning_bench_snapshot


def test_reasoning_status_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        os.environ["GEMMA_PROJECT_ROOT"] = td
        payload = {"score_percent": 97.5, "passed_cases": 39, "total_cases": 40, "ok": True}
        save_reasoning_bench_snapshot(payload)
        got = load_reasoning_bench_snapshot()
        assert got.get("score_percent") == 97.5
        assert got.get("passed_cases") == 39
        assert got.get("ok") is True
        os.environ.pop("GEMMA_PROJECT_ROOT", None)
