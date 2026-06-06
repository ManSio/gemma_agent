<p align="center">
  <img src="assets/social-preview.png" alt="Gemma Agent — Telegram assistant · OpenRouter" width="100%"/>
</p>

<!-- AGENT-ONLY: Контекст: CHATGPT_PASTE.ru.md или AGENTS.md. Cursor: .cursor/README.md -->

# Gemma Agent

**Telegram-ассистент для небольшого доверенного круга (3–8 человек)** — память, роутинг, tools по необходимости.

**[OpenRouter](https://openrouter.ai/)** (модель на выбор). Репозиторий — **оркестратор**: роутинг, память, guards, деплой.

| Часто думают | Что у нас |
|--------------|-----------|
| Только локальная LLM | Модели через **OpenRouter** |
| Framework как LangChain | **Telegram-продукт** с плагинами |
| Демо без тестов | **2580+** pytest, CI — [CI.md](docs/CI.md) |

**Коротко:** ассистент в Telegram (по умолчанию), power-agent — opt-in.

**По умолчанию:** ассистент (сообщение → роутинг → LLM → ответ). Агентные циклы — **opt-in** ([HONEST_POSITIONING](docs/HONEST_POSITIONING.md)).

<p align="center">
  <a href="https://github.com/ManSio/gemma_agent/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/ManSio/gemma_agent/ci.yml?branch=master&label=CI&style=for-the-badge" alt="CI"></a>
  <a href="docs/CI.md"><img src="https://img.shields.io/badge/тесты-2580%2B-brightgreen?style=for-the-badge" alt="Tests"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/лицензия-MIT-blue?style=for-the-badge" alt="License"></a>
  <a href="VERSION"><img src="https://img.shields.io/badge/версия-3.4.0-orange?style=for-the-badge" alt="Version"></a>
</p>

<p align="center">
  <a href="docs/index.ru.md"><img src="https://img.shields.io/badge/Документация-index-0ea5e9?style=for-the-badge" alt="Docs"></a>
  <a href="https://github.com/ManSio/gemma_agent"><img src="https://img.shields.io/badge/GitHub-ManSio-181717?style=for-the-badge&logo=github" alt="GitHub"></a>
  <a href="README.md"><img src="https://img.shields.io/badge/Lang-English-blue?style=for-the-badge" alt="EN"></a>
</p>

---

## С чего начать

| Кто вы | Читать |
|--------|--------|
| **Новый посетитель** | [docs/REPO_MAP.md](docs/REPO_MAP.md) |
| **Честные ограничения** | [HONEST_POSITIONING](docs/HONEST_POSITIONING.md) |
| **Запустить бота** | [quickstart.ru.md](docs/getting-started/quickstart.ru.md) |
| **Доказательства тестов и CI** | [docs/CI.md](docs/CI.md) |

```bash
python scripts/print_repo_stats.py
python -m pytest tests/ --collect-only -q
```

---

## Кратко

| | |
|---|---|
| **Аудитория** | 3–8 доверенных пользователей с одобрением админа |
| **Тесты** | **410** файлов · **2580+** кейсов — [`tests/`](tests/) · [`pytest.ini`](pytest.ini) |
| **CI** | [ci.yml](.github/workflows/ci.yml) — каждый PR: ruff + smoke + pytest + privacy |
| **Модули** | 19 плагинов (публичная сборка) |
| **Деплой** | systemd, Docker Compose, `gemma_panel.sh` |
| **Железо** | От **1 GB + VPN** до 4 GB VPS — [system requirements](docs/SYSTEM_REQUIREMENTS.md) |

---

## Честное позиционирование

**Хронология:** прод с **2026-05-02**; публичный GitHub с **2026-06-06** — не «репо на один день». Мало звёзд = узкий private-first scope.

| Вопрос | Ответ |
|--------|--------|
| Оверинжиниринг? | **По умолчанию — простой ассистент.** Healers / goal runner — opt-in |
| STM/MTM/LTM? | Три слоя хранения с файлами в коде — [MEMORY.md](docs/MEMORY.md) |
| Self-healing? | Healers + safe mode, не только try/except — [SELF_HEALING.md](docs/SELF_HEALING.md) |
| Агентность? | **6/10** с дефолтом, **7.5/10** с `power_agent` — [HONEST_POSITIONING.md](docs/HONEST_POSITIONING.md) |
| MetaGPT / OpenHands? | **Нет** — другая категория продукта |

---

## Возможности

| Возможность | По умолчанию | Примечание |
|-------------|:------------:|------------|
| Чат, роутинг, skills | **да** | каждый ход |
| Погода, поиск, новости | **да** | |
| Напоминания | **да** | |
| Память LTM | **да** | Mem0 |
| Goal runner (агент) | **нет** | `GOAL_RUNNER_ENABLED=false` |
| Self-verify / quality loop | **нет** | opt-in |
| Self-healing | частично | healers по env |
| MCE / spatial | нет | public build |

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

## Docker

```bash
cp .env.example .env
docker compose build
docker compose up -d app
```

На малом VPS (1 GB + VPN) — **native** через `gemma_panel.sh` (проверено). Docker: [DEPLOY.md](docs/DEPLOY.md).

SearXNG в Docker (опц.): `cd infra/searxng && docker compose up -d`  
**Деплой:** [docs/DEPLOY.md](docs/DEPLOY.md) · **Бэкап:** `bash scripts/backup.sh`

---

## Тесты и CI

GitHub Actions — [docs/CI.md](docs/CI.md)

```bash
pip install -r requirements-dev.txt
python scripts/print_repo_stats.py
python -m pytest tests/ -q
python scripts/release_guard.py --smoke   # = CI smoke job
```

[`pytest.ini`](pytest.ini) · [CI.md](docs/CI.md)

---

## Ключевые документы

| Тема | Ссылка |
|------|--------|
| **Карта репо** | [docs/REPO_MAP.md](docs/REPO_MAP.md) |
| **CI и тесты** | [docs/CI.md](docs/CI.md) |
| Цикл агента | [docs/AGENT_LOOP.md](docs/AGENT_LOOP.md) |
| Архитектура | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Cursor agent | [`.cursor/README.md`](.cursor/README.md) |
| Замена на public VPS | [migrate-from-private.ru.md](docs/getting-started/migrate-from-private.ru.md) |
| Память STM/MTM/LTM | [docs/MEMORY.md](docs/MEMORY.md) |
| Самоисцеление | [docs/SELF_HEALING.md](docs/SELF_HEALING.md) |
| Критерии приёмки | [docs/ACCEPTANCE_CRITERIA.md](docs/ACCEPTANCE_CRITERIA.md) |
| Системные требования | [docs/SYSTEM_REQUIREMENTS.md](docs/SYSTEM_REQUIREMENTS.md) |
| Деплой и бэкапы | [docs/DEPLOY.md](docs/DEPLOY.md) |
| Безопасность | [docs/security/security-model.ru.md](docs/security/security-model.ru.md) |
| Все доки | [docs/index.ru.md](docs/index.ru.md) |

---

## Ресурсы

| | |
|---|---|
| [Contributing](CONTRIBUTING.md) | Вклад в проект |
| [Security](SECURITY.md) | Политика безопасности |
| [Лицензия MIT](LICENSE) | |
| [Code of Conduct](CODE_OF_CONDUCT.md) | |

---

## Проверка перед релизом

```bash
python scripts/release_guard.py
python scripts/check_public_privacy.py --ci
python scripts/agent_security_audit.py
```

