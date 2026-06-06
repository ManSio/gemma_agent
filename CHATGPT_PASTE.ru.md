# Контекст проекта (офлайн-выжимка)

Репозиторий: https://github.com/ManSio/gemma_agent  
**Полный FAQ:** [docs/HONEST_POSITIONING.md](docs/HONEST_POSITIONING.md) · **Клон лучше**, чем paste без git.

---

## Что это

**Telegram-ассистент** для **3–8 доверенных пользователей** (не SaaS). Python, aiogram, **OpenRouter** (GPT/Claude/Gemini на выбор).

| Тема | Факт |
|------|------|
| Имя `gemma_agent` | Название проекта — **не** Google Gemma |
| Интеллект | Модели через OpenRouter |
| Репозиторий | Оркестрация: роутинг, память, tools, guards, ops |
| Дефолт | **Ассистент** (route → LLM → ответ) |
| Агентные циклы | Opt-in (`GOAL_RUNNER_ENABLED`, power_agent) |

**Хронология:** прод с **2026-05-02**; публичный GitHub **2026-06-06**.

---

## Проверка в git

```bash
python scripts/print_repo_stats.py
python -m pytest tests/ --collect-only -q
```

| Факт | Значение |
|------|----------|
| Тесты | 410 файлов, **2580+** кейсов |
| CI | `.github/workflows/ci.yml` |
| Плагины (public) | 19 |
| Hot path | `input_layer` → `orchestrator` → `brain/pipeline` |

Raw: ветка **`master`**.

---

## Ограничения

- Не multi-tenant; только доверенный круг
- Prompt injection смягчён, не устранён полностью
- Mem0 stub — слабый substring; лучше Mem0 server
- **pytest ≠ живой Telegram** — [PRODUCTION_EVIDENCE_REPORT.ru.md](docs/PRODUCTION_EVIDENCE_REPORT.ru.md) §10
- Сырые prod-логи приватны; в отчёте — агрегаты

---

## Прод-снимок (май–июнь 2026)

Подробно: [docs/PRODUCTION_EVIDENCE_REPORT.ru.md](docs/PRODUCTION_EVIDENCE_REPORT.ru.md)

| Метрика | Было → Стало |
|---------|------------|
| median prompt `brain_first` | 10 255 → 3 134 tok |
| VPS p90 | 107 s → ~20 s |
| Ошибки 14d | 56+16 → 0 |
| Расходы | ~€2–5/мес VPS + ~$0.0003/вызов LLM |

---

## Техдолг

| | |
|--|--|
| `orchestrator.py` | ~4250 строк |
| `.env.example` | ~960 ключей (lab heritage) |

---

## Карта доков

| Тема | Файл |
|------|------|
| Позиционирование | [docs/HONEST_POSITIONING.md](docs/HONEST_POSITIONING.md) |
| Архитектура | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Cursor | [.cursor/README.md](.cursor/README.md) |
| Безопасность | [SECURITY.md](SECURITY.md) |

Оценки maintainer'а — в HONEST_POSITIONING (самооценка, не сертификация).
