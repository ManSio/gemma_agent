# Repository map — read this first

One-page orientation for humans and AI reviewers landing on GitHub.

---

## What is this?

**Gemma Agent** — Telegram assistant for **3–8 trusted users**.  
Python + OpenRouter + plugins. **Not** a local-Gemma hack, **not** Claude Code, **not** public SaaS.

| Fact | Evidence in repo |
|------|------------------|
| Tests | `tests/` — **407** files, **2573+** cases — [`pytest.ini`](../pytest.ini) |
| CI | [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) — runs on every PR |
| Docker | [`Dockerfile`](../Dockerfile), [`docker-compose.yml`](../docker-compose.yml) |
| Docs | `docs/` — architecture, memory, deploy, security |
| Version | [`VERSION`](../VERSION) — currently 3.4.x |

```bash
python scripts/print_repo_stats.py   # refresh numbers locally
```

**AI reviewers:** read [AGENTS.md](../AGENTS.md) before scoring.

---

## Directory map

```
gemma_agent/
├── main.py, api.py          # Entry points
├── core/                    # Brain, orchestrator, guards, resilience
├── modules/                 # 19 plugins (public build), each has module.json
├── config/                  # modules_catalog.json, env fragments
├── tests/                   # 407 test_*.py files, 2573+ cases
├── scripts/                 # bootstrap, panel, release_guard, backup
├── docs/                    # Full documentation hub
├── .github/workflows/       # CI (ci.yml, release-guard.yml)
├── Dockerfile               # Container image
├── docker-compose.yml       # Local Docker deploy
├── pytest.ini               # Pytest config (visible from repo root)
├── pyproject.toml           # Project metadata + ruff
├── AGENTS.md                # Guide for AI code reviewers
└── .env.example             # All env keys (copy to .env)
```

---

## I want to…

| Goal | Start here |
|------|------------|
| **Understand architecture** | [ARCHITECTURE.md](ARCHITECTURE.md) |
| **Run the bot** | [getting-started/quickstart.md](getting-started/quickstart.md) |
| **See test & CI proof** | [CI.md](CI.md) · [ACCEPTANCE_CRITERIA.md](ACCEPTANCE_CRITERIA.md) |
| **Deploy on VPS** | [DEPLOY.md](DEPLOY.md) · [SYSTEM_REQUIREMENTS.md](SYSTEM_REQUIREMENTS.md) |
| **Security boundaries** | [security/security-model.md](security/security-model.md) |
| **Contribute** | [CONTRIBUTING.md](../CONTRIBUTING.md) |
| **Agent loop (plan/verify)** | [AGENT_LOOP.md](AGENT_LOOP.md) |

---

## CI at a glance

| Check | Command (local) | Automated |
|-------|-----------------|-----------|
| Syntax | `ruff check --select E9 …` | ✅ `ci.yml` |
| Smoke tests | `release_guard.py --smoke` | ✅ `ci.yml` |
| Full pytest | `pytest tests/ -q` | ✅ `ci.yml` + `release-guard.yml` |
| Privacy scan | `check_public_privacy.py --ci` | ✅ `ci.yml` |
| Security audit | `agent_security_audit.py` | ✅ `ci.yml` |

Details: [CI.md](CI.md)

---

## Common wrong first impressions

| People say | Reality |
|------------|---------|
| "No tests" | 2573+ pytest cases in `tests/` |
| "No CI" | `.github/workflows/ci.yml` |
| "Just a script bot" | 19 plugins, brain pipeline, healers |
| "Needs local Gemma GPU" | OpenRouter API — any model you choose |
| "1 GB impossible" | Proven on legacy VPS — [SYSTEM_REQUIREMENTS.md](SYSTEM_REQUIREMENTS.md) |

---

## Full doc index

[docs/index.md](index.md) · [docs/llms.txt](llms.txt)
