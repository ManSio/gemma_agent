import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from core import llm_usage_store as store


@pytest.fixture
def tmp_log(monkeypatch):
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = f.name
    monkeypatch.setenv("GEMMA_LLM_USAGE_PATH", path)
    monkeypatch.setenv("GEMMA_LLM_USAGE_PERSIST", "true")
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


def _row_at(
    *,
    days_ago: float,
    total_tokens: int = 100,
    kind: str = "chat",
    cost: float = 0.0,
    ok: bool = True,
) -> dict:
    ts = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ok": ok,
        "requested_model": "openrouter/free",
        "upstream_model": "x",
        "kind": kind,
        "total_tokens": total_tokens,
        "prompt_tokens": 40,
        "completion_tokens": 60,
        "cost": cost,
    }


def test_append_and_aggregate(tmp_log):
    store.append_record(_row_at(days_ago=1, total_tokens=100))
    store.append_record(
        _row_at(days_ago=0, total_tokens=200, kind="vision", cost=0.01)
    )

    agg = store.aggregate_usage(days=30.0)
    assert agg["total_tokens"] == 300
    assert "vision" in agg["by_kind"]
    assert agg["by_kind"]["vision"]["tokens"] == 200
    assert pytest.approx(agg["cost_sum"], rel=1e-6) == 0.01


def test_aggregate_excludes_records_outside_window(tmp_log):
    """Регрессия CI: фиксированные даты в прошлом выпадали из days=30."""
    store.append_record(_row_at(days_ago=45, total_tokens=999))
    store.append_record(_row_at(days_ago=2, total_tokens=50))

    agg = store.aggregate_usage(days=30.0)
    assert agg["total_tokens"] == 50
    assert agg["window_records"] == 1


def test_aggregate_ignores_failed_completions_in_token_sum(tmp_log):
    store.append_record(_row_at(days_ago=0, total_tokens=500, ok=True))
    store.append_record(_row_at(days_ago=0, total_tokens=900, ok=False))

    agg = store.aggregate_usage(days=7.0)
    assert agg["total_tokens"] == 500
    assert agg["completions_fail"] == 1
    assert agg["completions_ok"] == 1


def test_aggregate_rows_override_skips_file(tmp_log):
    rows = [
        _row_at(days_ago=0, total_tokens=11),
        _row_at(days_ago=0, total_tokens=22),
    ]
    agg = store.aggregate_usage(days=30.0, rows=rows)
    assert agg["total_tokens"] == 33
    assert agg["window_records"] == 2


def test_recent_rows_and_sort(tmp_log):
    now = datetime.now(timezone.utc)
    for i, cost in enumerate([0.05, 0.01, 0.1]):
        store.append_record(
            {
                "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "ok": True,
                "kind": "chat",
                "total_tokens": 50 + i,
                "cost": cost,
            }
        )
    recent = store.recent_rows(days=1.0)
    assert len(recent) >= 3
    by_cost = store.sorted_records(recent, sort="cost", limit=2)
    assert float(by_cost[0].get("cost") or 0) >= float(by_cost[1].get("cost") or 0)


def test_unicode_sparkline():
    s = store.unicode_sparkline([0.0, 1.0, 2.0, 4.0])
    assert len(s) == 4


def test_reset_records(tmp_log):
    store.append_record(_row_at(days_ago=0, total_tokens=123))
    rep = store.reset_records()
    assert rep.get("ok") is True
    assert store.load_records() == []


def test_news_generation_log_redacts_sensitive_fields(tmp_log):
    """Persisted news_generation rows keep aggregates only (CodeQL alert-autofix)."""
    row = store.news_generation_log(
        user_id="42",
        query="секретный запрос",
        sources=[{"url": "https://example.com/x", "domain": "example.com", "fetch_method": "web_search", "parsing_confidence": 0.5}],
        reply="секретный ответ",
    )
    store.append_record(row)
    loaded = store.load_records()
    assert len(loaded) == 1
    persisted = loaded[0]
    assert persisted["type"] == "news_generation"
    assert persisted["total_sources"] == 1
    assert "sources" not in persisted
    assert "query" not in persisted
    assert "reply" not in persisted
    assert "user_id" not in persisted


def test_persist_disabled_does_not_write(tmp_log, monkeypatch):
    monkeypatch.setenv("GEMMA_LLM_USAGE_PERSIST", "false")
    store.append_record(_row_at(days_ago=0, total_tokens=1))
    assert store.load_records() == []
