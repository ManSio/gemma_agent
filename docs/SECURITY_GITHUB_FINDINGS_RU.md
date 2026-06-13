# Security findings on GitHub — status (2026-06-13)

## Code scanning (CodeQL)

| Rule | Count (before fix wave) | Mitigation |
|------|-------------------------|------------|
| `py/clear-text-logging-sensitive-data` | 20 | `core/sensitive_export.py`, Mem0 path facets, connectivity scalars |
| `py/clear-text-storage-sensitive-data` | 6 | typed writers, `render_audit_document_md`, `barrierModel` extensions |
| `py/incomplete-url-substring-sanitization` | 1 | `urlparse` in tests |
| `py/polynomial-redos` | 19 | `core/regex_safe.py` cap + pattern fixes (2026-06-13 wave 4) |

**Wave 4 (2026-06-13):** CI `test_finalize_send_path_inventory` — полное тело `_send_output`; CodeQL ReDoS — `regex_safe`, `write_daily_ops_md`, bounded patterns.

**Wave 3 (2026-06-13):** alerts #41, #49, #105, #117–#120 — typed writers, count-only stdout, `write_audit_document_md`. CodeQL config без unpublished pack (локальный extension pack остаётся для VS Code).

**Workflow:** `.github/workflows/codeql.yml` — re-scan on push + weekly.

**Stale alerts:** after merge, GitHub may need one CodeQL run to close fixed alerts (Security → Code scanning).

**Open autofix PRs #22, #23** — закрыты (superseded by `master`).

## aiohttp CVE (игнор до bump aiogram)

| CVE | Суть | Риск для gemma_agent |
|-----|------|----------------------|
| CVE-2026-34993 | `CookieJar.load()` + pickle | **Нет** — `CookieJar` в репо не используется |
| CVE-2026-47265 | cookies при cross-origin redirect | **Низкий** — `cookies=` в aiohttp-запросах нет |

**Почему не апгрейдим сейчас:** `aiogram 3.28.2` требует `aiohttp<3.14`; патчи только в `3.14+`. Последний aiogram на PyPI (2026-06-13) — всё ещё `<3.14`.

### Watchlist (проверять ~раз в месяц)

1. PyPI aiogram — появился ли релиз с `aiohttp>=3.14` в Requires-Dist:  
   `python -m pip index versions aiogram` + metadata / [pypi.org/project/aiogram](https://pypi.org/project/aiogram/)
2. Если да — один PR:
   - `requirements.txt` → `aiohttp>=3.14.0,<3.15`
   - убрать `--ignore-vuln CVE-2026-*` из `scripts/pip_audit.sh`
   - полный `pytest` + CI + smoke TG
3. Запись в `DEV_DIARY_RU.md` + обновить эту секцию.

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
