# For coding agents and contributors

Entry point for humans and tools working in this repository.

**Cursor (IDE):** [.cursor/README.md](.cursor/README.md) — rules, skills, prompts.  
**Offline context:** [CHATGPT_PASTE.md](CHATGPT_PASTE.md) · [RU](CHATGPT_PASTE.ru.md)  
**Positioning & limits:** [docs/HONEST_POSITIONING.md](docs/HONEST_POSITIONING.md)  
**Raw GitHub:** branch `main` or `master` — e.g. `raw.githubusercontent.com/ManSio/gemma_agent/main/SECURITY.md`

---

## What this project is

- **Telegram assistant** for **3–8 trusted users** — not mass SaaS
- **LLM:** OpenRouter (configurable models)
- **Name `gemma_agent`:** historical — not Google Gemma runtime
- **Default:** assistant path; multi-step agent is **opt-in**
- **Version:** [VERSION](VERSION)

---

## Verify (do not assume)

```bash
python scripts/print_repo_stats.py
python -m pytest tests/ --collect-only -q
python scripts/release_guard.py --smoke
```

| Claim | Where |
|-------|-------|
| Tests | `tests/` — 410 files, 2580+ cases |
| CI | `.github/workflows/ci.yml` |
| Architecture | `docs/ARCHITECTURE.md`, `docs/REPO_MAP.md` |
| Security | `SECURITY.md`, `docs/security/security-model.md` |
| Prod metrics method | `docs/PRODUCTION_EVIDENCE_REPORT.md` |

---

## Documented limitations

1. Not multi-tenant — trusted circle by design  
2. Prompt injection mitigated, not eliminated  
3. Mem0 stub — weak retrieval; use Mem0 server for better search  
4. Single-server scale — no horizontal autoscaling  
5. Default chat-first — `GOAL_RUNNER_ENABLED=false` unless enabled  
6. Not an IDE coding agent (not Claude Code / OpenHands category)  

Details: [docs/HONEST_POSITIONING.md](docs/HONEST_POSITIONING.md)

---

## Key entry points

| Task | Doc |
|------|-----|
| Repo map | [docs/REPO_MAP.md](docs/REPO_MAP.md) |
| Honest positioning | [docs/HONEST_POSITIONING.md](docs/HONEST_POSITIONING.md) |
| CI & tests | [docs/CI.md](docs/CI.md) |
| Architecture | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Agent loops | [docs/AGENT_LOOP.md](docs/AGENT_LOOP.md) |
| Cursor setup | [.cursor/README.md](.cursor/README.md) |
| Public vs private build | [docs/getting-started/public-build.md](docs/getting-started/public-build.md) |

---

## Hot path (do not break without tests)

`input_layer` → `orchestrator` → `call_brain` → `response_adapter`
