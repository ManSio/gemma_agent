# Gemma Agent Documentation

Telegram assistant for a **small trusted circle** — **OpenRouter**, memory, routing, tools when needed.

| | |
|---|---|
| Version | `3.4.0` (see [VERSION](../VERSION)) |
| Modules (public build) | **19** active plugins |
| Tests | **410** files · **2580+** cases — [CI.md](CI.md) |
| Honest positioning | [HONEST_POSITIONING.md](HONEST_POSITIONING.md) — reviewer Q&A |
| CI | [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) — every push/PR |
| First visit | [REPO_MAP.md](REPO_MAP.md) · [AGENTS.md](../AGENTS.md) |
| Repo | Set URLs in [REPO_LINKS.md](REPO_LINKS.md) |

<p align="center">
  <img src="assets/pipeline-overview.svg" alt="Pipeline" width="640"/>
</p>

---

## Quick links

| Section | What's covered |
|---------|----------------|
| [Quickstart](getting-started/quickstart.md) | First working bot in ~15 minutes |
| [Installation](getting-started/installation.md) | Bootstrap, venv, panel, services |
| [Configuration](getting-started/configuration.md) | `.env`, OpenRouter, access control |
| [Learning path](getting-started/learning-path.md) | Where to read next by role |
| [Telegram usage](user-guide/telegram.md) | How to talk to the bot |
| [Admin & ops](user-guide/admin-ops.md) | `/diag`, logs, diagnostics |
| [Panel](user-guide/panel.md) | `gemma_panel.sh` commands |
| [Troubleshooting](user-guide/troubleshooting.md) | Common failures |
| [Features overview](features/overview.md) | What the bot can do (honest) |
| [Web search](features/web-search.md) | SearXNG setup |
| [Memory](features/memory.md) | Mem0 stub vs local vs cloud |
| [Voice](features/voice.md) | STT/TTS (Piper, Vosk) |
| [Modules](features/modules.md) | 19 plugins in public build |
| [Security](security/security-model.md) | What is protected — and what is not |
| [Honest positioning](HONEST_POSITIONING.md) | Assistant vs agent, scores, test quality, retrieval |
| [Production evidence](PRODUCTION_EVIDENCE_REPORT.md) | May–Jun metrics: tokens −70%, costs, latency, tests |
| [Repo map](REPO_MAP.md) | Read this first — tests, CI, layout |
| [CI & tests](CI.md) | GitHub Actions jobs + local commands |
| [Agent loop](AGENT_LOOP.md) | Plan → Execute → Verify (honest) |
| [Architecture](ARCHITECTURE.md) | Layers + Mermaid diagram |
| [Memory tiers](MEMORY.md) | STM / MTM / LTM |
| [Self-healing](SELF_HEALING.md) | Healers, safe mode, fallbacks |
| [Acceptance criteria](ACCEPTANCE_CRITERIA.md) | Test gates and evidence |
| [System requirements](SYSTEM_REQUIREMENTS.md) | Proven hardware (VPS + LAN) |
| [Deployment](DEPLOY.md) | VPS, Docker, backups |
| [Architecture (detailed)](developer-guide/architecture.md) | Code layout, message path |
| [Testing](developer-guide/testing.md) | pytest, release_guard, smoke |
| [Contributing](../CONTRIBUTING.md) | PR process (GitHub Contributing tab) |
| [Publish checklist](PUBLISH_CHECKLIST.md) | Create repo, About, topics, releases |
| [Environment variables](reference/environment-variables.md) | Key `.env` keys |
| [Scripts CLI](reference/scripts-cli.md) | `gemma_panel`, bootstrap, audit |

**Russian:** [index.ru.md](index.ru.md)

---

## For LLMs and coding agents

- [llms.txt](llms.txt) — curated doc index (~15 KB)
- Regenerate: `python scripts/generate_llms_txt.py`

---

## Public vs private build

This tree is the **public** export. Owner-only modules (spatial, law search, 47 dormant plugins) are removed. Details: [public-build.md](getting-started/public-build.md).
