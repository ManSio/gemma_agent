# Contributing to Gemma Agent

Thank you for helping improve Gemma Agent. This guide covers setup, architecture, what we merge, and how to open a good PR.

**Russian:** [docs/developer-guide/contributing.ru.md](docs/developer-guide/contributing.ru.md)  
**Docs hub:** [docs/index.md](docs/index.md)

---

## Contribution priorities

We value contributions in this order:

1. **Bug fixes** — wrong replies, crashes, data loss, security issues.
2. **Documentation** — fixes, setup clarity, honest security boundaries.
3. **Tests** — regression tests for fixed bugs; contract tests for plugins.
4. **Small features** — within existing modules (tier A/B in public build).
5. **Large features** — discuss in an issue first (scope and maintenance).

We do **not** merge: secrets in git, prod-only owner hacks, or “ideal stack” rewrites without issue agreement.

---

## Public build scope

This repository is the **public** export (19 plugins). Not included: spatial_design, LawSearch runtime, MCE auto on prod, 47 dormant modules.

Details: [docs/getting-started/public-build.md](docs/getting-started/public-build.md)

---

## Development setup

### Prerequisites

| Requirement | Notes |
|-------------|-------|
| **Python 3.11+** | 3.12 OK for CI |
| **Git** | |
| **Linux / macOS / Git Bash** | Windows: dev smoke via `python main.py` |
| **Telegram bot token** | @BotFather — your own test bot |
| **OpenRouter API key** | For LLM turns |

### Clone and install

```bash
git clone https://github.com/ManSio/gemma-agent.git
cd gemma-agent
bash scripts/agent_bootstrap.sh
```

Edit `.env`:

```env
TELEGRAM_TOKEN=
OPENROUTER_API_KEY=
ADMIN_USER_IDS=
USER_ACCESS_APPROVAL_REQUIRED=true
```

### Run locally

```bash
bash scripts/gemma_panel.sh start-all
python scripts/gemma_status.py --online
```

Optional services: [SearXNG](docs/features/web-search.md), [Mem0](docs/features/memory.md) — stub starts automatically with bootstrap.

### Replace `ManSio`

Before publishing the repo, set org/name in [config/repo_links.json](config/repo_links.json) and search-replace in README / issue templates.

---

## Architecture (60 seconds)

```
Telegram → input_layer → orchestrator → chat_orchestrator → brain/pipeline
         → response_adapter → Telegram
```

| Path | Role |
|------|------|
| `main.py` | Entry, plugin load |
| `core/input_layer.py` | Telegram ingress |
| `core/orchestrator.py` | plan + execute |
| `core/brain/pipeline.py` | LLM turn |
| `modules/` | Plugins (`module.json` each) |
| `config/modules_catalog.json` | Tier A/B catalog |

Deep dive: [docs/developer-guide/architecture.md](docs/developer-guide/architecture.md)

**Do not break** without tests: `input_layer` → `orchestrator` → `call_brain` → `response_adapter`.

---

## Local checks (required before PR)

```bash
python -m pytest tests/ -q
python scripts/release_guard.py
python scripts/check_public_privacy.py --ci
python scripts/agent_security_audit.py --quick
```

| Gate | Command | When |
|------|---------|------|
| Smoke + anti-regression | `release_guard.py` | Every PR |
| Privacy scan | `check_public_privacy.py --ci` | Every PR |
| Full suite | `release_guard.py --full` | Large changes |
| Security audit | `agent_security_audit.py` | Security-related PRs |

CI runs `release-guard` workflow on push/PR — see [.github/workflows/release-guard.yml](.github/workflows/release-guard.yml).

---

## Pull request process

1. **Open an issue** for non-trivial changes (or link existing).
2. **Branch** from `main`: `fix/weather-slot`, `docs/panel`, `feat/reminder-…`
3. **Small diff** — one logical change per PR.
4. **Conventional Commits** encouraged: `fix(dialogue): clear weather slot`, `docs: update panel`
5. **Fill PR template** — reproduction, tests, checklist.
6. **No secrets** — run privacy scan; never commit `.env`.

Maintainers may ask for Telegram smoke steps if behavior changes.

---

## Code guidelines

- Match existing style in the file you edit.
- New env vars → comment in [`.env.example`](.env.example).
- Plugin changes → keep `module.json` contract valid (`pytest tests/test_plugin_contract.py`).
- Router/guard changes → add or extend regression test.
- Comments only for non-obvious business logic.

### Plugins

Public build ships **19** modules. New plugins:

- Add `modules/<name>/module.json` + `module.py`
- Register in `config/modules_catalog.json` if tier A/B
- Tests in `tests/test_*` — at least smoke

Authoring reference: [docs/features/modules.md](docs/features/modules.md)

---

## Documentation

| Change | Update |
|--------|--------|
| Setup / env | `docs/getting-started/`, `.env.example` |
| New script | `docs/reference/scripts-cli.md` |
| Behavior | `docs/user-guide/` or `docs/features/` |
| Security | `docs/security/security-model.md`, `SECURITY.md` |

Regenerate LLM index: `python scripts/generate_llms_txt.py`

---

## Security

Read [SECURITY.md](SECURITY.md) before reporting vulnerabilities.

- Report security issues **privately** (GitHub Security Advisories after repo is public).
- Do not open public issues with exploit details or token dumps.

Honest limits: [docs/security/security-model.md](docs/security/security-model.md)

---

## What we won't merge without discussion

- `TELEGRAM_PIPELINE_PRIVATE_PARALLEL>1` without lock regression tests
- MCE / goal runner enabled by default on production paths
- Large refactors of `core/brain/pipeline.py` for style only
- Dependencies not justified in the PR description
- Docs that claim “unhackable” or hide known limitations

---

## Community

| Resource | Link |
|----------|------|
| Documentation | [docs/index.md](docs/index.md) |
| Troubleshooting | [docs/user-guide/troubleshooting.md](docs/user-guide/troubleshooting.md) |
| Issues | GitHub Issues (after publish) |
| Security | [SECURITY.md](SECURITY.md) |

---

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
