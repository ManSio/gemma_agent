<p align="center">
  <img src="assets/gemma-agent-logo.png" alt="Gemma Agent" width="120"/>
</p>

# Gemma Agent

**Telegram-ассистент для небольшого доверенного круга** — память, роутинг, tools по необходимости.

<p align="center">
  <a href="docs/index.ru.md"><img src="https://img.shields.io/badge/Документация-index-0ea5e9?style=for-the-badge" alt="Docs"></a>
  <a href="https://github.com/ManSio/gemma_agent"><img src="https://img.shields.io/badge/GitHub-ManSio-181717?style=for-the-badge&logo=github" alt="GitHub"></a>
  <a href="README.md"><img src="https://img.shields.io/badge/Lang-English-blue?style=for-the-badge" alt="EN"></a>
</p>

Публикация репо: [docs/PUBLISH_CHECKLIST.md](docs/PUBLISH_CHECKLIST.md)

---

## Ресурсы (как на GitHub у Hermes)

| | |
|---|---|
| [Документация](docs/index.ru.md) | Полный гайд |
| [Contributing](CONTRIBUTING.md) | Вклад в проект — вкладка **Contributing** на GitHub |
| [Security](SECURITY.md) | Политика безопасности |
| [Лицензия MIT](LICENSE) | |
| [Code of Conduct](CODE_OF_CONDUCT.md) | |

---

## Быстрая установка

```bash
git clone https://github.com/ManSio/gemma_agent.git /opt/gemma_agent
cd /opt/gemma_agent
bash scripts/agent_bootstrap.sh
# .env — TELEGRAM_TOKEN, OPENROUTER_API_KEY, ADMIN_USER_IDS
bash scripts/gemma_panel.sh start-all
python scripts/gemma_status.py --online
```

**Полный гайд:** [docs/getting-started/quickstart.ru.md](docs/getting-started/quickstart.ru.md)

---

## Возможности

| Возможность | Статус |
|-------------|--------|
| Чат, роутинг, skills | да |
| Погода, поиск, напоминания | да (SearXNG + OpenRouter) |
| Память Mem0 | да (stub или server) |
| Картинки / vision | да |
| Голос STT/TTS | опционально |
| MCE / mesh / spatial | нет (public) |

---

## Документация

| Раздел | Ссылка |
|--------|--------|
| Оглавление | [docs/index.ru.md](docs/index.ru.md) |
| Установка | [docs/getting-started/installation.ru.md](docs/getting-started/installation.ru.md) |
| Конфигурация | [docs/getting-started/configuration.ru.md](docs/getting-started/configuration.ru.md) |
| Безопасность | [docs/security/security-model.ru.md](docs/security/security-model.ru.md) |
| Архитектура | [docs/developer-guide/architecture.ru.md](docs/developer-guide/architecture.ru.md) |
| Тесты | [docs/developer-guide/testing.ru.md](docs/developer-guide/testing.ru.md) |

Для LLM: [docs/llms.txt](docs/llms.txt)

---

## Перед релизом

```bash
python scripts/release_guard.py
python scripts/check_public_privacy.py --ci
python scripts/agent_security_audit.py
```
