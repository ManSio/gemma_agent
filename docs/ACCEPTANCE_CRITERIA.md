# Acceptance criteria (public build)

How we verify quality before calling a release “green”. **Run these commands** — do not assume absence of tests.

---

## Hard gates (must pass)

| Gate | Command | What it proves |
|------|---------|----------------|
| Smoke | `python scripts/release_guard.py --smoke` | Plugin contracts, compile, docs lint |
| Anti-regression | `python scripts/release_guard.py` | 90 targeted regression tests |
| Full suite | `python scripts/release_guard.py --full` | Entire pytest collection |
| Privacy | `python scripts/check_public_privacy.py --ci` | No secrets in tracked files |
| Security audit | `python scripts/agent_security_audit.py` | Config + boundary checklist |
| CI | `.github/workflows/ci.yml` | Every push/PR: ruff → smoke → full pytest → privacy |
| CI details | [CI.md](CI.md) | Job breakdown + local equivalents |

---

## Test inventory (evidence)

| Metric | Value (verify with `print_repo_stats.py`) |
|--------|------------------------------------------|
| Test files | **410** (`tests/test_*.py`) |
| Test cases | **2580+** (`pytest tests/ --collect-only`) |
| Anti-regression | **90** files in `release_guard.py` |
| Config | `pytest.ini`, `pyproject.toml` |
| CI workflows | `ci.yml`, `release-guard.yml`, `mutation-l2.yml` |

```bash
python scripts/print_repo_stats.py          # all counts
python -m pytest tests/ --collect-only -q   # exact case count
python -m pytest tests/test_plugin_contract.py -q
python -m pytest tests/test_security_layer.py -q
```

Representative areas:

| Area | Tests |
|------|-------|
| Plugins | `test_plugin_contract.py`, `test_modules_catalog.py` |
| Security | `test_security_layer.py`, `test_access_gate.py` |
| Memory | `test_memory_*.py`, `test_mem0_merge.py` |
| Brain / routing | `test_pipeline_routing.py`, `test_orchestrator_*.py` |
| Resilience | `test_resilience_controller.py`, `test_event_healers.py` |
| Product UX | `test_product_behavior.py`, `test_user_facing_contract.py` |

### Test quality (not count alone)

| Layer | Proves |
|-------|--------|
| Product | pivot, search gates — `test_product_behavior.py` |
| Multi-turn | pending interrupt — `test_pending_flow.py` |
| Honesty | no fake sources — `test_acc11_honest_refusal.py` |
| Routing | intent→module — `test_orchestrator_intent_routing.py` |
| Ship gate | 90 anti-regression files in `release_guard.py` |

Mixed suite includes unit/helper tests too. See [developer-guide/testing.md](developer-guide/testing.md) · [HONEST_POSITIONING.md](HONEST_POSITIONING.md)

---

## Behavioral contracts

| Contract | Verified by |
|----------|-------------|
| Plugin `module.json` valid | `test_plugin_contract.py` |
| Admin-only commands gated | `test_admin_access.py`, `test_access_gate.py` |
| Honest refusal (no fake sources) | `test_acc11_honest_refusal.py` |
| Context not unbounded | `test_context_compression.py`, `test_compactor.py` |
| Flood / rate limits | `test_flood_gating.py`, `test_api_rate_limit.py` |

---

## What green CI does **not** mean

- Live Telegram verified (manual: `gemma_status.py --online`)
- Prompt injection immunity (documented limitation)
- Multi-tenant production SaaS readiness

See [security/security-model.md](security/security-model.md).

---

## Release checklist

```bash
python scripts/release_guard.py --full
python scripts/check_public_privacy.py --ci
python scripts/agent_security_audit.py
```

Details: [PUBLISH_CHECKLIST.md](PUBLISH_CHECKLIST.md), [developer-guide/testing.md](developer-guide/testing.md)
