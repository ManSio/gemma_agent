# Gemma Agent Documentation

Telegram assistant for a **small trusted circle** — dialogue memory, smart routing, tools when needed.

| | |
|---|---|
| Version | `3.4.0` (see [VERSION](../VERSION)) |
| Modules (public build) | **19** active plugins |
| Tests | `pytest tests/` — run [release_guard](../scripts/release_guard.py) before release |
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
| [Architecture](developer-guide/architecture.md) | Code layout, message path |
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
