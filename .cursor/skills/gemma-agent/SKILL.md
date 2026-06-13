---
name: gemma-agent
description: >-
  Guides all work on ManSio/gemma_agent — Python fixes, features, security,
  docs, and audits with Opus-style discipline. Use in this repo for orchestrator,
  Telegram bot, OpenRouter, plugins, tests, access_gate, or when user wants
  minimal diff, verify-before-done, or trace flow before edit.
---

# Gemma Agent — project workflow

Apply on **every task** in this repository: audit, bugfix, feature, refactor, docs.

## Order (never skip)

1. **Orient** — user goal; read callers and tests first; **bugfix → `docs/DEV_DIARY_RU.md` (last 3 entries)**.
2. **Trace** — input → decision → action → output. Fix root cause, not symptom.
3. **Investigate** — read/grep/run **before** edit or verdict.
4. **Act** — minimal diff; match file style; no drive-by refactors.
5. **Verify** — pytest/ruff/smoke/grep; state what was **not** run.
6. **Report** — changed files, verified vs assumed.
7. **Document** — if behavior/limits/CI/security changed: `CHANGELOG.md` + entry in `docs/DEV_DIARY_RU.md` + runbook if needed (`docs/CONTEXT_BUDGET_GUIDE_RU.md` pattern).

## Ground truth

- Telegram assistant, **3–8 users**, OpenRouter — **not** Google Gemma.
- Public GitHub **2026-06-06**; prod from **2026-05-02** — not "1 day old".
- Maintainer score tables = **self-assessment**, not external consensus.
- **pytest ≠ Telegram** (20 May parallel-DM bug documented).

## Hot path (do not break without tests)

`input_layer` → `orchestrator` → `call_brain` → `response_adapter`

## Python changes

| Area | Use |
|------|-----|
| Paths | `core/safe_paths.py` |
| Private access | `core/access_gate.py` early |
| Log/export redaction | `core/sensitive_export.py` |
| New flags | `.env.example` + comment |
| Plugins | `module.json` + `test_plugin_contract.py` |
| Orchestrator | surgical edits only (~4256 LOC monolith) |

## Verify (pick by task)

```bash
python scripts/release_guard.py --smoke
python -m pytest tests/test_<name>.py -q
python -m ruff check <paths>
python scripts/check_public_privacy.py --ci
```

## Never

- Guess file contents; invent SECURITY/CONTRIBUTING after 404.
- Over-engineer one-liner helpers.
- Commit unless user explicitly asks.
- User correction overrides — apply immediately.

## More detail

- Verify matrix, audit template, traps: [reference.md](reference.md)
- Deep audit only: skill `gemma-deep-audit`
- User paste prompts: `.cursor/prompts/`
- Hub: `.cursor/README.md`
