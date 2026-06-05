# Документация Gemma Agent

Telegram-ассистент для **небольшого круга** — память диалога, роутинг, tools по необходимости.

| | |
|---|---|
| Версия | `3.4.0` ([VERSION](../VERSION)) |
| Модули (public) | **19** плагинов |
| Тесты | `pytest tests/` + [release_guard](../scripts/release_guard.py) |
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
| [Архитектура](developer-guide/architecture.ru.md) | Код и пайплайн |
| [Тестирование](developer-guide/testing.ru.md) | pytest, smoke |
| [Contributing](../CONTRIBUTING.md) | Вклад в проект (вкладка GitHub) |
| [Публикация репо](PUBLISH_CHECKLIST.md) | About, topics, release |

**English:** [index.md](index.md)

---

## Для LLM-агентов

- [llms.txt](llms.txt)
- `python scripts/generate_llms_txt.py`

---

## Public-сборка

[public-build.ru.md](getting-started/public-build.ru.md) — что урезано относительно private fork.
