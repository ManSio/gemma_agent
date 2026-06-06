---
name: gemma-deep-audit
description: >-
  Performs disciplined deep audits of ManSio/gemma_agent — architecture, security,
  tests, production claims. Use when user asks for audit, review, оценка, вердикт,
  analysis of repo, or Opus-style repository review.
---

# Gemma deep audit

Stack with skill **gemma-agent**. Read [reference.md](reference.md) for output template.

## Before any verdict

Run (or equivalent with tools):

```bash
python scripts/print_repo_stats.py
python -m pytest tests/ --collect-only -q | tail -1
wc -l core/orchestrator.py
grep -cE '^[A-Z_][A-Z0-9_]*=' .env.example
```

Read minimum:
- `SECURITY.md`
- `docs/HONEST_POSITIONING.md`
- `docs/PRODUCTION_EVIDENCE_REPORT.md` §0, §10
- Topic-specific paths user named

## Rules

1. No score for unread files — say **not read**.
2. Label: **verified** | **documented** | **assumed** | **cannot verify**.
3. Retract wrong assumptions when evidence contradicts.
4. Independent rubric scores — do not copy maintainer 9/10 tables as consensus.
5. Cite code as `startLine:endLine:path`.

## Must mention if relevant

- Orchestrator ~4256 LOC; `.env.example` ~963 keys (documented debt)
- Default = assistant; `goal_runner` opt-in
- Prod jsonl private — aggregates only in report

## Output

Use template in [reference.md](reference.md).

User paste: `.cursor/prompts/audit.md` or `audit.ru.md`
