# Кэш и задержки при ответе — runbook метрик

Операторский справочник: **откуда брать цифры**, как их читать, что считается «нормой» на текущем проде (VPS, 3–8 пользователей, OpenRouter).

Связанные документы: [PRODUCTION_EVIDENCE_REPORT.ru.md](PRODUCTION_EVIDENCE_REPORT.ru.md) (май–июнь, токены) · [admin-ops.ru.md](user-guide/admin-ops.ru.md) · [CONTEXT_BUDGET_GUIDE_RU.md](CONTEXT_BUDGET_GUIDE_RU.md)

---

## Быстрый снимок (рекомендуется)

На сервере (`/srv/gemma_bot`):

```bash
cd /srv/gemma_bot
PYTHONPATH=. venv/bin/python3 scripts/snapshot_cache_latency.py \
  --root . \
  --hours 24 \
  --json data/diagnostics/cache_latency_latest.json \
  --md data/diagnostics/cache_latency_latest.md
```

Скрипт печатает Markdown в stdout и опционально сохраняет JSON/MD в `data/diagnostics/`.

| Источник | Окно | Что даёт |
|----------|------|----------|
| `data/llm_usage.jsonl` | `--hours` (default 24) | brain latency, KV hit %, cached token % |
| `data/runtime/turns.jsonl` | то же | end-to-end `latency_ms`, разбивка `stage_ms` |
| `data/runtime/metrics_timeseries.jsonl` | последняя строка | накопительные счётчики MONITOR (кэш, reuse) |

**Важно:** in-memory OBS/MONITOR (`telegram_p95`, `openrouter_p95`) в отдельном процессе Python **пустые** — для «с boot» смотрите `metrics_timeseries.jsonl` или `/admin_self` из живого бота.

---

## Снимок prod 2026-06-13 (24h, VPS)

Полная копия: [archive/CACHE_LATENCY_SNAPSHOT_2026-06-13_RU.md](archive/CACHE_LATENCY_SNAPSHOT_2026-06-13_RU.md)

### Кэш

| Метрика | Значение | Комментарий |
|---------|----------|-------------|
| Brain LLM вызовов | 22 | строки brain в `llm_usage.jsonl` |
| KV hit (по вызовам) | **18.2%** | доля ответов с `cached_prompt_tokens > 0` |
| Доля cached prompt tokens | **42.6%** | cached / все prompt tokens |
| `brain_response_cache_hit` | 0 | детерминированный pre-LLM кэш ответов |
| `openrouter_prompt_reuse` | нет hits | reuse-счётчики = 0 в снимке MONITOR |
| recent в brain | r8=17, r12=5 | лимит recent_dialogue в brain-вызовах |

**Вывод:** OpenRouter KV даёт умеренную экономию токенов (~43% prompt tokens из кэша провайдера). Локальные short-circuit кэши за сутки не сработали.

### Задержки

| Слой | p50 | p95 |
|------|-----|-----|
| Полный ответ (turns) | 5.3 s | **20.9 s** |
| Только brain LLM | 4.7 s | **9.7 s** |
| Все LLM ok | — | 12.6 s |

**Разбивка pipeline (`turns.stage_ms`, p95):**

| Стадия | p95 | Смысл |
|--------|-----|--------|
| `exec_modules_done` | **20.9 s** | brain + модули (узкое место) |
| `total` | 21.5 s | весь ход |
| `plan_done` | 14.7 s* | выбросы в plan (медиана 93 ms) |
| `pre_plan` + `plan_*` | &lt; 200 ms | планировщик |

\* Хвост `plan_done` раздувается редкими выбросами; типичный plan — доли секунды.

**Вывод:** задержка ответа ≈ **время OpenRouter/brain**; оптимизация planner/pre_plan почти не влияет на p95.

---

## Источники данных

### 1. `llm_usage.jsonl`

Пишется из `core/llm_telemetry.py` / OpenRouter provider.

| Поле | Использование |
|------|----------------|
| `latency_ms` | задержка LLM-вызова |
| `cached_prompt_tokens` | токены из KV OpenRouter |
| `prompt_tokens` / `input_tokens` | полный prompt |
| `telemetry_tag` / `telemetry_kind` | фильтр `brain` |

Агрегация: `core/admin_ops_metrics.summarize_llm_usage_window()`.

Счётчики reuse: `openrouter_prompt_reuse_hits_total` / `_misses_total` в MONITOR (из `llm_telemetry` при разборе usage).

### 2. `turns.jsonl`

Пишется `core/turn_observer.py`.

| Поле | Использование |
|------|----------------|
| `latency_ms` | end-to-end до ответа в TG |
| `stage_ms` | сегменты OBS (`pre_plan`, `exec_modules_done`, …) |
| `prompt_tokens_est` | оценка размера промпта |
| `brain_recent_limit` | какой лимит recent попал в brain |

Агрегация: `summarize_turns_window()`, `snapshot_cache_latency._stage_ms_window()`.

### 3. `metrics_timeseries.jsonl`

Пишется autopilot (`MONITOR.persist_snapshot`) — снимки счётчиков MONITOR.

Полезные ключи (кэш / LLM):

- `brain_prompt_cache_hit_total`
- `brain_response_cache_hit_total`
- `openrouter_prompt_reuse_hits_total` / `_misses_total`
- `openrouter_prompt_cache_read_tokens_total`
- `openrouter_completion_ok_total` / `_fail_total`

### 4. In-memory (живой процесс бота)

- `core/observability.OBS` — p95 по `telegram_pipeline`, `openrouter_completion_ms`, …
- `core/monitoring.MONITOR` — счётчики событий

Доступ: `/admin_self`, `/admin_xray`, API `/api/v1/diagnostics` (если `API_ENABLED`).

---

## Другие скрипты

| Скрипт | Назначение |
|--------|------------|
| `scripts/metrics_period_report.py` | agent vs LLM по дням, фазы продукта, JSONL history |
| `scripts/daily_server_digest.py` | DAILY_OPS в `docs/archive/` |
| `scripts/server_full_audit.py` | недельный аудит |
| `scripts/analyze_kv_session_metrics.py` | KV session hit (legacy) |
| `scripts/turns_search.py` | поиск по turns |

Реестр полей периодов: `config/metrics_period_registry.json`.

---

## Telegram-команды (админ)

| Команда | Метрики |
|---------|---------|
| `/admin_self` | llm_24h, turns_24h, p95 brain/telegram |
| `/admin_xray` | pulse, anomalies, host pressure |
| `/admin_llm_usage` | расходы и токены |
| `/admin_diagnostic` | ZIP bundle |

---

## Интерпретация и действия

| Симптом | Где смотреть | Типичная причина |
|---------|--------------|------------------|
| Ответ &gt; 20 s p95 | `turns.stage_ms.exec_modules_done` | free-модель, таймаут, тяжёлый profile |
| KV hit &lt; 10% | `llm_usage` cached % | частая смена system prompt / session_id |
| `brain_response_cache_hit` = 0 | MONITOR | нет повторяющихся identity/weather short-circuit |
| `planner_fallback_total` растёт | metrics_timeseries | router/heuristic fallback |
| `issues` в turns | turns.jsonl | обрыв ответа, guard |

Тюнинг: `OP_TIMEOUT_SEC`, profile registry, `BRAIN_RECENT_LIMIT`, KV session stickiness — см. [CONTEXT_BUDGET_GUIDE_RU.md](CONTEXT_BUDGET_GUIDE_RU.md).

---

## basedpyright (IDE)

~900+ «ошибок» в Project Diagnostics — **статический type checker** (`pyproject.toml` → `[tool.basedpyright]`), не runtime и не pytest.

| Тип | Кол-во (порядок) | Смысл |
|-----|------------------|--------|
| `reportOptionalMemberAccess` | ~550 | `.get()` на Optional |
| `reportArgumentType` | ~290 | `str \| None` vs `str` |
| Warnings `__all__` lazy | 8 | `core/__init__.py` — ложные |
| Missing imports plugins | ~30 | optional modules в public build |

CI гоняет **ruff E9 + pytest**, не basedpyright. См. [CI.md](CI.md).

---

## Обновление этого документа

После значимых изменений pipeline или снятия нового prod-снимка:

1. Запустить `snapshot_cache_latency.py` на VPS.
2. Сохранить выжимку в `docs/archive/CACHE_LATENCY_SNAPSHOT_YYYY-MM-DD_RU.md`.
3. Запись в `docs/DEV_DIARY_RU.md` + `CHANGELOG.md`.

*Последнее обновление runbook: 2026-06-13.*
