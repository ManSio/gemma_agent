# gemma-agent — reference

## Verify commands

```bash
python scripts/print_repo_stats.py
python -m pytest tests/ --collect-only -q | tail -1
wc -l core/orchestrator.py
grep -cE '^[A-Z_][A-Z0-9_]*=' .env.example
```

## Verify by change type

| Change | Minimum |
|--------|---------|
| Any `.py` | Read lints on touched files |
| Routing / brain | `pytest tests/test_orchestrator_intent_routing.py -q` |
| Plugin | `pytest tests/test_plugin_contract.py -q` |
| Security / logging | `python scripts/check_public_privacy.py --ci` |
| **Before `git commit`** | `check_public_privacy.py --ci` **and** `agent_security_audit.py --ci` (both exit 0) |
| Product behavior | `python scripts/release_guard.py --smoke` |
| Full PR | `CONTRIBUTING.md` local checks |

## Verify vs trust

| In git | Trust operator |
|--------|----------------|
| pytest count, CI, code paths | Raw `turns.jsonl` rows |
| SECURITY.md content | VPS €/month exact |
| LOC, env key count | Independent security audit |

## Common traps

- Raw URL 404 on wrong branch → use `main` or clone
- Low GitHub stars ≠ no production
- Prefer clone over paste-only context

## Implementation report template

```markdown
## Done
- …

## Verified
- command / file read

## Not run
- …

## Risks / follow-up
- …
```

## Hub docs

`AGENTS.md` · `docs/HONEST_POSITIONING.md` · `docs/REPO_MAP.md` · `CONTRIBUTING.md`
