# Testing

**CI proof:** [CI.md](../CI.md) · **Counts:** `python scripts/print_repo_stats.py`

## Quick run

```bash
python scripts/print_repo_stats.py        # 410 files, 2580+ cases
python -m pytest tests/ -q
```

Public build target: **2580+** tests green (see release_guard).

## Test quality — not just count

High case count is verifiable; **what** is tested matters more for reviewers.

| Layer | Example files | Locks real behavior? |
|-------|---------------|----------------------|
| **Product contracts** | `test_product_behavior.py` | yes — pivot, search gates, commerce vs science |
| **Multi-turn UX** | `test_pending_flow.py` | yes — interrupt words, pending registry |
| **Honest answers** | `test_acc11_honest_refusal.py` | yes — no fake sources |
| **Orchestrator** | `test_orchestrator_intent_routing.py` | yes — intent → module (14 cases) |
| **Memory** | `test_mem0_merge.py`, `test_memory_slash_bridge.py` | yes — dedup, bridge |
| **Plugins** | `test_plugin_contract.py` | yes — every `module.json` |
| **Security** | `test_security_layer.py`, `test_flood_gating.py` | yes |
| **Unit / helpers** | many `test_*` files | partial — parsers, env, edge cases |
| **Ship gate** | 90 files in `release_guard.py` ANTI_REGRESSION_TESTS | yes — product anti-regression |

**Honest:** suite is mixed. Strength = **release_guard** + product/routing contracts, not “2580 trivial asserts”.

```bash
python scripts/release_guard.py           # 90 anti-regression files
pytest tests/test_product_behavior.py tests/test_pending_flow.py -q
```

See [ACCEPTANCE_CRITERIA.md](../ACCEPTANCE_CRITERIA.md) · [HONEST_POSITIONING.md](../HONEST_POSITIONING.md)

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
