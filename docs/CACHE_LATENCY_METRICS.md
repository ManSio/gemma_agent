# Cache & response latency metrics

Operator runbook: **where numbers come from**, how to read them, and what is “normal” on the current VPS deployment (3–8 users, OpenRouter).

Related: [PRODUCTION_EVIDENCE_REPORT.md](PRODUCTION_EVIDENCE_REPORT.md) · [admin-ops.md](user-guide/admin-ops.md) · Russian: [CACHE_LATENCY_METRICS_RU.md](CACHE_LATENCY_METRICS_RU.md)

---

## Quick snapshot (recommended)

On the server:

```bash
cd /srv/gemma_bot   # or your GEMMA_BOT_DIR
PYTHONPATH=. venv/bin/python3 scripts/snapshot_cache_latency.py \
  --root . --hours 24 \
  --json data/diagnostics/cache_latency_latest.json \
  --md data/diagnostics/cache_latency_latest.md
```

| Source | Window | Metrics |
|--------|--------|---------|
| `data/llm_usage.jsonl` | `--hours` (default 24) | brain latency, KV hit %, cached token % |
| `data/runtime/turns.jsonl` | same | end-to-end `latency_ms`, `stage_ms` breakdown |
| `data/runtime/metrics_timeseries.jsonl` | last line | cumulative MONITOR counters |

**Note:** OBS/MONITOR p95 from a **separate** Python process is empty — use `metrics_timeseries.jsonl` or live `/admin_self` from the bot process.

---

## Prod snapshot 2026-06-13 (24h)

Details: [archive/CACHE_LATENCY_SNAPSHOT_2026-06-13_RU.md](archive/CACHE_LATENCY_SNAPSHOT_2026-06-13_RU.md) (counts only, no PII).

| Area | Highlight |
|------|-----------|
| OpenRouter KV | **18.2%** call hit rate, **42.6%** cached prompt tokens |
| Brain LLM p95 | **9.7 s** |
| User-facing p95 | **20.9 s** (`turns.latency_ms`) |
| Bottleneck | `exec_modules_done` ≈ brain/LLM time |

---

## Data sources

- **`llm_usage.jsonl`** — per LLM call; aggregation in `core/admin_ops_metrics.summarize_llm_usage_window()`
- **`turns.jsonl`** — per Telegram turn; `stage_ms` from `core/turn_observer.py`
- **`metrics_timeseries.jsonl`** — periodic MONITOR snapshots (autopilot)
- **Live bot** — `OBS` / `MONITOR`; `/admin_self`, `/admin_xray`

Other scripts: `metrics_period_report.py`, `daily_server_digest.py`, `turns_search.py` — see [scripts-cli.md](reference/scripts-cli.md).

---

## IDE diagnostics (basedpyright)

~900+ editor “errors” are **static typing** (`pyproject.toml`), not pytest failures. CI runs ruff E9 + pytest only — [CI.md](CI.md).

---

*Last updated: 2026-06-13.*
