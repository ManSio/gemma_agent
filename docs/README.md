# Documentation index

Wiki hub for the public Gemma Agent tree. Start here or use [index.md](index.md) (same structure, richer intro).

| | |
|---|---|
| English hub | [index.md](index.md) |
| Russian hub | [index.ru.md](index.ru.md) |
| Repo URLs | [REPO_LINKS.md](REPO_LINKS.md) |
| Publish steps | [PUBLISH_CHECKLIST.md](PUBLISH_CHECKLIST.md) |
| **Repo map (read first)** | [REPO_MAP.md](REPO_MAP.md) |
| **CI & tests proof** | [CI.md](CI.md) |
| **GitHub About text** | [GITHUB_ABOUT.md](GITHUB_ABOUT.md) |
| **Honest positioning (hub)** | [HONEST_POSITIONING.md](HONEST_POSITIONING.md) |
| **Dev diary (агент: читать первым)** | [DEV_DIARY_RU.md](DEV_DIARY_RU.md) |
| **Cursor agent** | [../.cursor/README.md](../.cursor/README.md) |

---

## Architecture & operations

| Doc | Topic |
|-----|--------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Layers + Mermaid diagram |
| [AGENT_LOOP.md](AGENT_LOOP.md) | Plan → Execute → Verify (honest defaults) |
| [MEMORY.md](MEMORY.md) | STM / MTM / LTM tiers |
| [CONTEXT_BUDGET_GUIDE_RU.md](CONTEXT_BUDGET_GUIDE_RU.md) | Hard limit 15K, compactor, token_efficiency runbook |
| [SELF_HEALING.md](SELF_HEALING.md) | Healers, safe mode, rollback |
| [ACCEPTANCE_CRITERIA.md](ACCEPTANCE_CRITERIA.md) | Test gates and evidence |
| [SYSTEM_REQUIREMENTS.md](SYSTEM_REQUIREMENTS.md) | Proven hardware (1 GB–4 GB) |
| [DEPLOY.md](DEPLOY.md) | VPS, Docker, systemd, backups |

---

## Getting started

| Doc | Topic |
|-----|--------|
| [quickstart.md](getting-started/quickstart.md) | First bot in ~15 min |
| [quickstart.ru.md](getting-started/quickstart.ru.md) | Быстрый старт (RU) |
| [installation.md](getting-started/installation.md) | Bootstrap, venv, panel |
| [installation.ru.md](getting-started/installation.ru.md) | Установка (RU) |
| [configuration.md](getting-started/configuration.md) | `.env`, OpenRouter, ACL |
| [configuration.ru.md](getting-started/configuration.ru.md) | Конфигурация (RU) |
| [learning-path.md](getting-started/learning-path.md) | Read order by role |
| [learning-path.ru.md](getting-started/learning-path.ru.md) | Маршрут обучения (RU) |
| [public-build.md](getting-started/public-build.md) | Public vs private export |
| [public-build.ru.md](getting-started/public-build.ru.md) | Public build (RU) |

## User guide

| Doc | Topic |
|-----|--------|
| [telegram.md](user-guide/telegram.md) | Talking to the bot |
| [telegram.ru.md](user-guide/telegram.ru.md) | Telegram (RU) |
| [admin-ops.md](user-guide/admin-ops.md) | `/diag`, diagnostics |
| [admin-ops.ru.md](user-guide/admin-ops.ru.md) | Админ и ops (RU) |
| [panel.md](user-guide/panel.md) | `gemma_panel.sh` |
| [panel.ru.md](user-guide/panel.ru.md) | Панель (RU) |
| [troubleshooting.md](user-guide/troubleshooting.md) | Common failures |
| [troubleshooting.ru.md](user-guide/troubleshooting.ru.md) | Troubleshooting (RU) |

## Features

| Doc | Topic |
|-----|--------|
| [overview.md](features/overview.md) | Capability overview |
| [overview.ru.md](features/overview.ru.md) | Обзор (RU) |
| [web-search.md](features/web-search.md) | SearXNG |
| [web-search.ru.md](features/web-search.ru.md) | Поиск (RU) |
| [memory.md](features/memory.md) | Mem0 modes |
| [memory.ru.md](features/memory.ru.md) | Память (RU) |
| [voice.md](features/voice.md) | STT/TTS |
| [voice.ru.md](features/voice.ru.md) | Голос (RU) |
| [modules.md](features/modules.md) | 19 public plugins |
| [modules.ru.md](features/modules.ru.md) | Модули (RU) |

## Security & development

| Doc | Topic |
|-----|--------|
| [security-model.md](security/security-model.md) | Trust boundaries |
| [security-model.ru.md](security/security-model.ru.md) | Модель безопасности (RU) |
| [architecture.md](developer-guide/architecture.md) | Code layout |
| [architecture.ru.md](developer-guide/architecture.ru.md) | Архитектура (RU) |
| [testing.md](developer-guide/testing.md) | pytest, release_guard |
| [testing.ru.md](developer-guide/testing.ru.md) | Тестирование (RU) |
| [contributing.md](developer-guide/contributing.md) | Contributor notes |
| [contributing.ru.md](developer-guide/contributing.ru.md) | Contributing (RU) |

## Reference

| Doc | Topic |
|-----|--------|
| [environment-variables.md](reference/environment-variables.md) | Key `.env` vars |
| [scripts-cli.md](reference/scripts-cli.md) | CLI scripts |
| [PLUGIN_AUTHOR_HANDBOOK_RU.md](PLUGIN_AUTHOR_HANDBOOK_RU.md) | Plugin author guide (SelfProgramming) |
| [PLUGIN_AI_CRITICAL_BRIEF.md](PLUGIN_AI_CRITICAL_BRIEF.md) | Short LLM brief for plugins |
| [assets/README.md](assets/README.md) | Diagram assets |
