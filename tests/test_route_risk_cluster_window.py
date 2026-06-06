"""Окно cluster_route_risk_recent: ISO ts в route_risk.jsonl."""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

from core.route_risk_cluster import cluster_route_risk_recent, record_ts_epoch


def test_record_ts_epoch_iso():
    rec = {"ts": "2026-05-27T18:29:14+00:00"}
    t = record_ts_epoch(rec)
    assert t > 1_700_000_000


def test_cluster_excludes_old_iso_stumbles(tmp_path, monkeypatch):
    path = tmp_path / "route_risk.jsonl"
    old = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
    rows = [
        {"ts": old, "error_type": "model", "intent": "math", "detail": "format_only_number_violated"},
        {"ts": old, "error_type": "unknown", "intent": "unknown", "detail": "referential_math"},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    monkeypatch.setenv("GEMMA_PROJECT_ROOT", str(tmp_path))
    (tmp_path / "data" / "runtime").mkdir(parents=True)
    path.rename(tmp_path / "data" / "runtime" / "route_risk.jsonl")

    pack = cluster_route_risk_recent(hours=6.0, min_count=2)
    assert pack["total_stumbles"] == 0
    assert pack["clusters"] == []
