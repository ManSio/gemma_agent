# Public build

Export from private `gemma_bot` for open distribution.

## Removed

- LawSearch, AduPadruchnik, ParentPortal, SchoolAssistant
- `spatial_design` and 47 dormant/slash modules (tier C/D/DEV)
- DEV_DIARY, Cursor rules, VPS/METRICS/incident docs, secrets

## Included

- **19 modules** (tier A + B): brain, skills, memory, external_apis, image, rag, …
- Legal/edu via UniversalSearch + UrlFetch + DocumentCorpus + BooksRAG (no dedicated LawSearch runtime)
- `spatial_design` stubs (intent always false)

## Checks (maintainer)

```bash
pytest tests/
python scripts/release_guard.py
python scripts/check_public_privacy.py --ci
python scripts/audit_public_build.py
```

Re-export from private: `python scripts/export_public_agent.py --dest /path/to/gemma_agent` (private repo only).
