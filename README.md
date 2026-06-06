<p align="center">
  <img src="assets/gemma-agent-logo.png" alt="Gemma Agent" width="120"/>
</p>

# Gemma Agent

**Telegram assistant for a small trusted circle (3–8 users)** — dialogue memory, smart routing, tools when needed.  
Uses **OpenRouter** (model of your choice). Not a local-tiny-Gemma hack, not a Claude Code clone.  
Not a public SaaS — honest security model, full test suite, CI on every PR.

<p align="center">
  <a href="https://github.com/ManSio/gemma_agent/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/ManSio/gemma_agent/ci.yml?branch=master&label=CI&style=for-the-badge" alt="CI"></a>
  <a href="docs/CI.md"><img src="https://img.shields.io/badge/tests-2573%2B-brightgreen?style=for-the-badge" alt="Tests"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue?style=for-the-badge" alt="License"></a>
  <a href="VERSION"><img src="https://img.shields.io/badge/version-3.4.0-orange?style=for-the-badge" alt="Version"></a>
</p>

<p align="center">
  <a href="docs/index.md"><img src="https://img.shields.io/badge/Docs-hub-0ea5e9?style=for-the-badge" alt="Docs"></a>
  <a href="https://github.com/ManSio/gemma_agent"><img src="https://img.shields.io/badge/GitHub-ManSio-181717?style=for-the-badge&logo=github" alt="GitHub"></a>
  <a href="README.ru.md"><img src="https://img.shields.io/badge/Lang-Русский-red?style=for-the-badge" alt="RU"></a>
</p>

---

## Start here

| You are… | Read first |
|----------|------------|
| **New visitor / AI reviewer** | [docs/REPO_MAP.md](docs/REPO_MAP.md) → [AGENTS.md](AGENTS.md) |
| **Want to run the bot** | [docs/getting-started/quickstart.md](docs/getting-started/quickstart.md) |
| **Want proof of tests & CI** | [docs/CI.md](docs/CI.md) → [docs/ACCEPTANCE_CRITERIA.md](docs/ACCEPTANCE_CRITERIA.md) |
| **Want architecture** | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |

```bash
python scripts/print_repo_stats.py          # verify test count & CI files
python -m pytest tests/ --collect-only -q   # 2573+ collected
```

---

## At a glance

| | |
|---|---|
| **Users** | 3–8 trusted people with admin approval |
| **Tests** | **407** files · **2573+** cases — [`tests/`](tests/) · [`pytest.ini`](pytest.ini) |
| **CI** | [`.github/workflows/ci.yml`](.github/workflows/ci.yml) — every push/PR: ruff + smoke + full pytest + privacy |
| **Modules** | 19 plugins (public build) |
| **Deploy** | Native systemd, Docker Compose, or panel scripts |
| **Hardware** | From **1 GB + VPN** to 4 GB VPS — [system requirements](docs/SYSTEM_REQUIREMENTS.md) |

---

## Capabilities

| Capability | Status |
|------------|--------|
| Chat, routing, skills | yes |
| Weather, web search, news | yes (Open-Meteo + SearXNG) |
| Reminders & schedule | yes |
| Long-term memory | yes (Mem0 stub or server) |
| Image / vision | yes (opt-in) |
| Voice STT/TTS | optional (Piper/Vosk) |
| Self-healing / safe mode | yes ([docs](docs/SELF_HEALING.md)) |
| Learning from 👎 feedback | yes (ephemeral autolearn) |
| MCE / mesh / spatial | no (public build) |

---

## Quick install (native)

```bash
git clone https://github.com/ManSio/gemma_agent.git /opt/gemma_agent
cd /opt/gemma_agent
bash scripts/agent_bootstrap.sh
# edit .env — TELEGRAM_TOKEN, OPENROUTER_API_KEY, ADMIN_USER_IDS
bash scripts/gemma_panel.sh start-all
python scripts/gemma_status.py --online
```

**Full guide:** [docs/getting-started/quickstart.md](docs/getting-started/quickstart.md)

---

## Docker

```bash
cp .env.example .env   # fill secrets
docker compose build
docker compose up -d app
```

Native deploy recommended on small VPS (1 GB + VPN — verified). Docker: see [DEPLOY.md](docs/DEPLOY.md).

SearXNG in Docker (optional): `cd infra/searxng && docker compose up -d`  
**Deploy guide:** [docs/DEPLOY.md](docs/DEPLOY.md) · **Backups:** `bash scripts/backup.sh`

---

## Tests & CI

**GitHub Actions** runs the same gates on every PR — see [docs/CI.md](docs/CI.md).

```bash
pip install -r requirements-dev.txt
python scripts/print_repo_stats.py            # test file count, workflows
python -m pytest tests/ -q                    # full suite (2573+)
python scripts/release_guard.py --smoke       # = CI smoke job (~1 min)
python scripts/release_guard.py               # smoke + 90 anti-regression tests
```

Config: [`pytest.ini`](pytest.ini) · [CI.md](docs/CI.md) · [testing.md](docs/developer-guide/testing.md)

---

## Key documentation

| Topic | Link |
|-------|------|
| **Repo map (first visit)** | [docs/REPO_MAP.md](docs/REPO_MAP.md) |
| **CI & tests proof** | [docs/CI.md](docs/CI.md) |
| Agent loop (Plan→Verify) | [docs/AGENT_LOOP.md](docs/AGENT_LOOP.md) |
| Architecture (Mermaid) | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Memory STM/MTM/LTM | [docs/MEMORY.md](docs/MEMORY.md) |
| Self-healing | [docs/SELF_HEALING.md](docs/SELF_HEALING.md) |
| Acceptance criteria | [docs/ACCEPTANCE_CRITERIA.md](docs/ACCEPTANCE_CRITERIA.md) |
| System requirements | [docs/SYSTEM_REQUIREMENTS.md](docs/SYSTEM_REQUIREMENTS.md) |
| Deployment & backups | [docs/DEPLOY.md](docs/DEPLOY.md) |
| Security (honest) | [docs/security/security-model.md](docs/security/security-model.md) |
| All docs | [docs/index.md](docs/index.md) |
| LLM index | [docs/llms.txt](docs/llms.txt) |

---

## Resources

| | |
|---|---|
| [Contributing](CONTRIBUTING.md) | Dev setup, PR process |
| [Security policy](SECURITY.md) | Report vulnerabilities |
| [MIT License](LICENSE) | |
| [Code of Conduct](CODE_OF_CONDUCT.md) | |

---

## Verify before release

```bash
python scripts/release_guard.py
python scripts/check_public_privacy.py --ci
python scripts/agent_security_audit.py
```
