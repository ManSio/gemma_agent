# DEV_DIARY_RU — журнал разработки

> **Для coding-агента (обязательно):** при багфиксе `gemma_bot` / brain / CI — **читай этот файл первым** (см. `.cursorrules`).
> После значимых изменений — **новая запись сверху** + блок в `CHANGELOG.md` + runbook, если менялось поведение или лимиты.

---

## Правила ведения (чтобы не ходить по кругу)

| Когда | Что сделать |
|-------|-------------|
| **Старт задачи** | Прочитать последние 3 записи ниже; skill `.cursor/skills/gemma-agent/SKILL.md`; при смене лимитов — `docs/CONTEXT_BUDGET_GUIDE_RU.md` |
| **Перед правкой** | Trace: `input → orchestrator → call_brain → response_adapter`; grep callers + tests |
| **После правки** | Запись в этот файл; `CHANGELOG.md`; обновить runbook / `PRODUCTION_EVIDENCE_REPORT*` если затронуты токены, CI, security |
| **Verify** | Минимум: targeted pytest + `release_guard --smoke`; в записи явно — что **не** гоняли |
| **Commit** | Только по явной просьбе Михаила |
| **Коррекция «нет / не то»** | Приоритет #1 — менять поведение сразу, логировать в записи |

**Не считать задачу закрытой**, если код смержен, а дневник/CHANGELOG/runbook не обновлены.

---

## 2026-06-13 — Hard context limit 15K, CI aiohttp, CodeQL sanitization

**Контекст:** план «День 1» — жёсткий лимит контекста ~15K токенов; на GitHub CI красный после dependabot merge.

### Проблема (root cause)

1. **Budget без зубов:** `token_efficiency.budget.enabled=true`, но `collapse.enabled=false` → при превышении `budget_hard_limit` вызывался `collapse_context()`, который сразу выходил (no-op). Промпт мог раздуваться до десятков тысяч токенов.
2. **YAML compactor сломан:** блок `compactor` был вложен в `budget` → `compactor_enabled()` всегда `False`.
3. **CI ResolutionImpossible:** dependabot поднял `aiohttp>=3.14.1`, `aiogram 3.28.2` требует `aiohttp<3.14`.
4. **CodeQL:** ~27 open alerts — clear-text logging/storage (mem0 path, heuristic_misses excerpt, audit scripts).

### Решение

| Компонент | Изменение |
|-----------|-----------|
| `core/context_collapse.py` | `enforce_context_limit()` — hard prune по приоритету ключей `prompt_parts` |
| `core/brain/pipeline.py` | После неудачного collapse → `enforce_context_limit` + reassemble |
| `config/token_efficiency.yml` | `hard_limit_tokens: 15000`, compactor на верхнем уровне, `enabled: true` |
| `requirements.txt` | `aiohttp>=3.9.0,<3.14` (совместимость с aiogram) |
| `core/mem0_memory/mem0_module.py` | `mem0_path_log_facets()` — логи без raw API path |
| `core/heuristic_misses_log.py` | `user_id_hash`, `text_excerpt_redacted` |
| `core/sensitive_export.py` | `mem0_path_log_facets()` helper |
| Скрипты audit | Только sanitized output в stdout/файлы |

### Метрики (MONITOR)

- `budget_exceeded_total`
- `context_hard_limit_enforced_total`
- `context_hard_limit_pruned_total`
- `compactor_triggered_total`

### Verify (локально)

```bash
python -m pytest tests/test_context_hard_limit.py tests/test_token_efficiency_config.py tests/test_sensitive_export.py -q
python scripts/release_guard.py --smoke
pip install -r requirements.txt   # без ResolutionImpossible
```

**Не гоняли:** полный `pytest tests/` (~2500+); Telegram smoke на VPS.

### Deploy

- Commit: `8d28b5a` на `master`
- После pull на VPS: перезапуск бота (`gemma_panel.sh`); YAML кеш `token_efficiency` ~30 с
- Смотреть: `[brain] prompt_metrics`, `context_hard_limit_enforced_total`

### Документация (эта сессия)

- `docs/CONTEXT_BUDGET_GUIDE_RU.md` — runbook
- `CHANGELOG.md`, `PRODUCTION_EVIDENCE_REPORT*.md`, `ARCHITECTURE.md`, `docs/README.md`

---

## 2026-06-08 — News Reliability Hardening (v3.5.0)

См. `CHANGELOG.md` · runbook: `docs/NEWS_RELIABILITY_GUIDE.md` · commit на `master` после merge PR news-модулей.

Ключевое: `NewsArticle`/`NewsValidator`/`NewsDisclaimerGenerator`, self-verify с источниками, 37 тестов `test_news_*`.

---

## 2026-06-05 — spatial_design v1 (v3.4.0)

См. `CHANGELOG.md` · `docs/SPATIAL_DESIGN_V1_RU.md`.

---

## Шаблон новой записи

```markdown
## YYYY-MM-DD — Краткий заголовок

**Контекст:** …

### Проблема (root cause)
…

### Решение
…

### Verify
…

### Deploy
…
```
