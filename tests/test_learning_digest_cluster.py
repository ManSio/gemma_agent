import json
import time

from core.learning_digest import build_learning_digest, format_learning_digest_html
from core.route_risk_cluster import cluster_route_risk_recent


def test_cluster_route_risk_groups(tmp_path, monkeypatch):
    rt = tmp_path / "data" / "runtime"
    rt.mkdir(parents=True)
    p = rt / "route_risk.jsonl"
    now = time.time()
    rows = [
        {"ts": now, "error_type": "tool", "intent": "general", "module": "chat", "fp": "abc", "detail": "x"},
        {"ts": now, "error_type": "tool", "intent": "general", "module": "chat", "fp": "abc", "detail": "y"},
        {"ts": now, "error_type": "policy", "intent": "news", "module": "brain", "fp": "zzz"},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    monkeypatch.setenv("GEMMA_PROJECT_ROOT", str(tmp_path))
    pack = cluster_route_risk_recent(hours=1.0, min_count=2)
    assert pack["total_stumbles"] == 3
    assert len(pack["clusters"]) >= 1
    assert pack["clusters"][0]["count"] >= 2


def test_build_learning_digest_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMMA_PROJECT_ROOT", str(tmp_path))
    d = build_learning_digest()
    assert "lessons" in d
    assert "experience" in d


def test_build_learning_digest_route_risk_splits_quality_loop(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMMA_PROJECT_ROOT", str(tmp_path))
    rt = tmp_path / "data" / "runtime"
    rt.mkdir(parents=True)
    now = time.time()
    p = rt / "route_risk.jsonl"
    p.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": now,
                        "error_type": "model",
                        "severity": 3,
                        "detail": "quality_loop:reply_echo",
                        "module": "chat",
                    }
                ),
                json.dumps(
                    {
                        "ts": now,
                        "error_type": "policy",
                        "severity": 3,
                        "detail": "timeout",
                        "module": "brain",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    d = build_learning_digest()
    rr = d.get("route_risk") or {}
    assert rr.get("stumbles_total") == 2
    assert rr.get("quality_loop_stumbles") == 1
    assert rr.get("stumbles") == 1


def test_format_learning_digest_html_route_risk_quality_loop_note():
    digest = {
        "experience": {"window_hours": 24, "ok": 1, "bad": 0},
        "lessons": {"active_lessons": 0},
        "route_risk": {"stumbles": 5, "quality_loop_stumbles": 100},
    }
    html = format_learning_digest_html(digest)
    assert "quality_loop" in html
    assert "100" in html
