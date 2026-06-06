# Документация Gemma Agent

Telegram-ассистент для **небольшого круга** — память диалога, роутинг, tools по необходимости.

| | |
|---|---|
| Версия | `3.4.0` ([VERSION](../VERSION)) |
| Модули (public) | **19** плагинов |
| Тесты | **407** файлов · **2573+** кейсов — [CI.md](CI.md) |
| CI | [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) — каждый PR |
| Первый визит | [REPO_MAP.md](REPO_MAP.md) · [AGENTS.md](../AGENTS.md) |
| Репозиторий | URL в [REPO_LINKS.md](REPO_LINKS.md) |

---

## Быстрые ссылки

| Раздел | О чём |
|--------|--------|
| [Быстрый старт](getting-started/quickstart.ru.md) | Первый рабочий бот за ~15 минут |
| [Установка](getting-started/installation.ru.md) | Bootstrap, venv, панель, сервисы |
| [Конфигурация](getting-started/configuration.ru.md) | `.env`, OpenRouter, доступ |
| [Маршрут обучения](getting-started/learning-path.ru.md) | Что читать по роли |
| [Telegram](user-guide/telegram.ru.md) | Как общаться с ботом |
| [Админ и ops](user-guide/admin-ops.ru.md) | `/diag`, логи |
| [Панель](user-guide/panel.ru.md) | `gemma_panel.sh` |
| [Проблемы](user-guide/troubleshooting.ru.md) | Частые сбои |
| [Возможности](features/overview.ru.md) | Что умеет (честно) |
| [Поиск](features/web-search.ru.md) | SearXNG |
| [Память](features/memory.ru.md) | Mem0 |
| [Голос](features/voice.ru.md) | STT/TTS |
| [Модули](features/modules.ru.md) | 19 плагинов |
| [Безопасность](security/security-model.ru.md) | Границы защиты |
| [Карта репо](REPO_MAP.md) | С чего начать — тесты, CI, структура |
| [CI и тесты](CI.md) | GitHub Actions + локальные команды |
| [Цикл агента](AGENT_LOOP.md) | Plan → Execute → Verify (честно) |
| [Архитектура](ARCHITECTURE.md) | Слои + Mermaid |
| [Память STM/MTM/LTM](MEMORY.md) | Три уровня памяти |
| [Самоисцеление](SELF_HEALING.md) | Healers, safe mode |
| [Критерии приёмки](ACCEPTANCE_CRITERIA.md) | Гейты и доказательства |
| [Системные требования](SYSTEM_REQUIREMENTS.md) | Проверенное железо (VPS + LAN) |
| [Деплой](DEPLOY.md) | VPS, Docker, бэкапы |
| [Архитектура (детально)](developer-guide/architecture.ru.md) | Код и пайплайн |
| [Тестирование](developer-guide/testing.ru.md) | pytest, smoke |
| [Contributing](../CONTRIBUTING.md) | Вклад в проект (вкладка GitHub) |
| [Публикация репо](PUBLISH_CHECKLIST.md) | About, topics, release |

**English:** [index.md](index.md)

---

## Для LLM-агентов

- [AGENTS.md](../AGENTS.md) — сначала это (чтобы не путать с MVP)
- [llms.txt](llms.txt)
- `python scripts/generate_llms_txt.py`

---

## Public-сборка

[public-build.ru.md](getting-started/public-build.ru.md) — что урезано относительно private fork.
