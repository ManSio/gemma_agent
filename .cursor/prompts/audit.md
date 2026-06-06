Audit **ManSio/gemma_agent**. Skills: `gemma-deep-audit` + `gemma-agent`.

## Topic
[architecture / security / tests / module X]

## Mandatory
```bash
python scripts/print_repo_stats.py
python -m pytest tests/ --collect-only -q | tail -1
wc -l core/orchestrator.py
grep -cE '^[A-Z_][A-Z0-9_]*=' .env.example
```

Read: `SECURITY.md`, `docs/HONEST_POSITIONING.md`, `docs/PRODUCTION_EVIDENCE_REPORT.md` §0 §10.

## Rules
- Verdict **after** files and commands
- File not read → say "not read", do not score it
- Public GitHub **2026-06-06**, prod from **2026-05-02** — not "1 day old"
- 9/10 tables in docs = **self-assessment**, not consensus
- Output: `.cursor/skills/gemma-deep-audit/reference.md`
