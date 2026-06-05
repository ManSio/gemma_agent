# Testing

## Quick run

```bash
python -m pytest tests/ -q
```

Public build target: **2500+** tests green (see release_guard).

## Gates before release

```bash
python scripts/release_guard.py           # smoke + anti-regression
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
