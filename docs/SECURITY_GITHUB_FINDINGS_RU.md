# Security findings on GitHub — status (2026-06-13)

## Code scanning (CodeQL)

| Rule | Count (before fix wave) | Mitigation |
|------|-------------------------|------------|
| `py/clear-text-logging-sensitive-data` | 20 | `core/sensitive_export.py`, Mem0 path facets, connectivity scalars |
| `py/clear-text-storage-sensitive-data` | 6 | `write_public_json_file`, `build_heuristic_miss_row`, audit sanitizers |
| `py/incomplete-url-substring-sanitization` | 1 | `urlparse` in tests |

**Workflow:** `.github/workflows/codeql.yml` — re-scan on push + weekly.

**Stale alerts:** after merge, GitHub may need one CodeQL run to close fixed alerts (Security → Code scanning).

**Open autofix PRs #22, #22** — superseded by `master`; close manually.

## Dependency audit (pip-audit)

| Package | Note |
|---------|------|
| `aiohttp<3.14` | Required by `aiogram 3.28`; CVE-2026-34993, CVE-2026-47265 **ignored** in `scripts/pip_audit.sh` until aiogram allows 3.14+ |
| Other deps | `pip-audit` clean as of 2026-06-13 |

## Operator checklist

```bash
python scripts/agent_security_audit.py --ci
python scripts/check_public_privacy.py --ci
bash scripts/pip_audit.sh
python -m pytest tests/test_sensitive_export.py -q
```

Runbook: [CONTEXT_BUDGET_GUIDE_RU.md](CONTEXT_BUDGET_GUIDE_RU.md) · Dev log: [DEV_DIARY_RU.md](DEV_DIARY_RU.md)
