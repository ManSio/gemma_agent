# Test suite

Gemma Agent public build: **2580+** pytest cases in **410** files.

Config: [`pytest.ini`](../pytest.ini) · CI: [CI.md](../docs/CI.md) · Criteria: [ACCEPTANCE_CRITERIA.md](../docs/ACCEPTANCE_CRITERIA.md)

## Run

```bash
python scripts/print_repo_stats.py          # verify counts
python -m pytest tests/ -q
python -m pytest tests/ --collect-only -q   # exact count
python -m pytest tests/test_security_layer.py -q
```

## Release gates

```bash
python scripts/release_guard.py           # smoke + anti-regression (90 tests)
python scripts/release_guard.py --full      # entire suite
```

## Layout

| Pattern | Purpose |
|---------|---------|
| `test_plugin_contract.py` | Every plugin `module.json` valid |
| `test_product_behavior.py` | User-facing contracts |
| `test_pending_flow.py` | Multi-step Telegram flows |
| `test_memory_*.py` | Mem0 / memory plugin |
| `test_external_apis*.py` | Weather and HTTP clients |

## What tests do not cover

- Live Telegram (manual or `gemma_status.py --online`)
- SearXNG / Mem0 HTTP on your host
- Prompt injection resistance (LLM boundary)

See [Testing](../docs/developer-guide/testing.md).
