# Testing

**CI proof:** [CI.md](../CI.md) · **Counts:** `python scripts/print_repo_stats.py`

## Quick run

```bash
python scripts/print_repo_stats.py        # 407 files, 2573+ cases
python -m pytest tests/ -q
```

Public build target: **2573+** tests green (see release_guard).

## Gates before release

```bash
python scripts/release_guard.py --smoke   # = CI smoke job (~1 min)
python scripts/release_guard.py           # smoke + 90 anti-regression tests
python scripts/release_guard.py --full    # + full pytest
python scripts/check_public_privacy.py --ci
python scripts/agent_security_audit.py
```

## Layers

| Layer | Command | Time |
|-------|---------|------|
| Smoke | `release_guard.py --smoke` | ~1 min |
| Anti-regression | `release_guard.py` | ~5–15 min |
| Full | `release_guard.py --full` | long |

## Key test areas

- `tests/test_plugin_contract.py` — plugin manifest
- `tests/test_security_layer.py` — security module
- `tests/test_product_behavior.py` — UX contracts
- `tests/test_pending_flow.py` — multi-turn flows

## Live Telegram

Green pytest ≠ verified in TG. After behavior changes: manual smoke or `gemma_status.py --online`.

## tests/README.md

See repo `tests/README.md` for layout.
