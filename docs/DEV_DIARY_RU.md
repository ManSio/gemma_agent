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

## 2026-06-13 — CI + CodeQL wave 4 (regex_safe, daily digest writer)

**Контекст:** CI fail `test_finalize_send_path_inventory`; 20 open CodeQL (19 ReDoS + 1 clear-text-storage).

**Сделано:**
- `core/regex_safe.py` — cap input, safe re.*, strip_trailing_sentence_punct, collapse_whitespace.
- Патчи на все flagged call sites + bounded regex в dialogue_slots/product/batch/code.
- `write_daily_ops_md` + CodeQL barrier для `daily_server_digest.py`.
- Тест `_send_output`: поиск `reply_text_chunks` в полном теле функции.

**Verify:** full pytest; `release_guard --smoke`.

---

## 2026-06-13 — docs sync: актуальные публичные счётчики

**Контекст:** AI-превью проекта показало, что README/docs местами сохранили старые счётчики тестов и размер monolith.

**Сделано:**
- `README.md`, `README.ru.md` — тесты `2779+`, файлы `440+`, версия `3.5.16`.
- `docs/REPO_MAP.md`, `docs/ARCHITECTURE.md`, `docs/HONEST_POSITIONING.md`, `docs/CI.md`, `docs/PRODUCTION_EVIDENCE_REPORT.md` — синхронизированы с локальной проверкой.
- `core/orchestrator.py` уточнён до `~4400` строк, `.env.example` до `~990` keys.

**Verify:** `python scripts/print_repo_stats.py`, `python -m pytest tests/ --collect-only -q`, grep по старым счётчикам в основных README/docs.

## 2026-06-13 — outbound guard v3.5.16 + intent/audit glue

**Сделано:**
- `outbound_thread_guard` — pre_send detect/recover на immediate followup.
- `intent_hint_from_turn_meaning` в `_detect_intent`.
- `turn_meaning_audit_for_emit` — multi-source fallback.
- discourse/turn_meaning в output.meta для pre_send.
- `turn.pre_send` → `turn_observer`: `outbound_thread_guard_issues` enrich по `trace_id` в turns.jsonl.

**Verify:** `pytest tests/test_outbound_thread_guard.py tests/test_turn_observer_pre_send.py …`; `release_guard --smoke`.

---

## 2026-06-13 — turn decision spine v3.5.15 (post-reconcile glue)

**Контекст:** code review — ephemeral до discourse, profile override затирался classifier; не short-fix.

**Сделано:**
- `turn_decision_spine` — refresh после reconcile: ephemeral + `meaning_profile_lock`.
- `discourse_thread_contract` — публичный API нити.
- orchestrator / `build_operator_corrections_hint` — без stale ephemeral cache.
- `apply_meaning_profile_lock` в конце `pipeline_routing`.
- `deactivate_legacy_generic_rating_lessons` + script для prod.

**Verify:** `pytest tests/test_turn_decision_spine.py …`; `release_guard --smoke`.

---

## 2026-06-13 — immediate thread followup v3.5.14 (thread followup «почему так»)

**Контекст:** prod — после ответа про Землю «почему так произошло?» уходило в meta про агента; не keyword-fix, а порядок решений + 👎 loop.

**Сделано:**
- `immediate_thread_followup` — novel content-токены vs last Q/A, порог `DISCOURSE_NOVEL_TOPIC_TOKENS`.
- substantive `почему` не branch, если followup по контракту; ACTIVE_THREAD + IUR rewrite.
- `/new` сбрасывает remarks и article_thread slot.
- `core/feedback_contract.py` — 👎 уроки по `anchor_user_q`, фильтр legacy generic на immediate followup; `brain_addon_for_text(text, ctx)`.
- `profile_override_from_meaning` в `pipeline_routing` (`TURN_MEANING_AGENT_PROFILE=standard`).

**Verify:** `pytest tests/test_discourse_resolver.py tests/test_turn_meaning.py tests/test_feedback_contract.py tests/test_prod_thread_wratmak.py -q`.

---

## 2026-06-13 — Turn shortcut gate v3.5.13 (P0: meaning до weather/geo)

**Контекст:** аудит — planner shortcuts (`weather_direct`, geo, pre_llm) шли до TurnMeaning; prod: погода/футер на identity-вопросах.

**Сделано:**
- `core/turn_shortcut_gate.py` — `prepare_plan_turn_gate`, `planner_shortcut_allowed`, slot bind для weather.
- `orchestrator.plan()` — gate всех direct shortcuts; inject early meaning в `pre_ctx`.
- `turn_meaning` — structural referent user/agent до substantive_question.
- `turn_reconcile` — skip повторного structural если `turn_meaning` уже в context.
- VERSION → 3.5.13.

**Verify:** `pytest tests/test_turn_shortcut_gate.py tests/test_turn_meaning.py tests/test_prod_thread_wratmak.py -q`; `release_guard --smoke`.

---

## 2026-06-13 — TurnMeaning v3.5.12 (смысл хода + judge bypass)

**Контекст:** аудит — thread judge обходился из-за `_turn_state_collapsed` в plan(); «слова вместо смысла» на referent (agent vs world).

**Сделано:**
- `core/turn_meaning.py` — structural verdict из метаданных + LLM judge (referent, speech_act).
- `turn_reconcile` — meaning → discourse → collapse; async upgrade после sync plan.
- `discourse_resolver` — приоритет `turn_meaning`; skip double judge при `source=llm`.
- `/new` чистит `pending_correction`; `SelfHealingEngine.get_instance()`.
- VERSION → 3.5.12.

**Verify:** `pytest tests/test_turn_meaning.py tests/test_prod_thread_wratmak.py … -q`; `release_guard --smoke`.

---

## 2026-06-13 — TurnStateVector + slot_registry (коллапс хода)

**Контекст:** уйти от разрозненных regex/слотов — один наблюдаемый вектор на ход.

**Сделано:**
- `core/turn_state.py` — TSV: discourse + slot before/after + prior_outcome + expects_correction.
- `core/slot_registry.py` — контракты `accepts_turn` (как profile_registry).
- `turn_reconcile` → `collapse_turn_state`; audit в `turns.jsonl`.
- `ARCHITECTURE.md` — диаграмма collapse.

**Verify:** `pytest tests/test_turn_state.py tests/test_turn_reconcile.py … -q` (37+ cases).

---

## 2026-06-13 — Аудит prod: залипший weather_await_city + drift → correct (без новых regex)

**Контекст:** prod — футер «погода» на нерелевантных темах; «я про другое» после ответа не по теме → дамп user_facts.

**Корень (архитектура, не keyword-списки):**
- Слот `weather_await_city` не имел контракта «принять/отклонить реплику» — залипал в behavior store.
- Discourse catch-all `structural` наследовал нить даже когда `classify_short_user_turn=normal` и ответ бота не совпадал с последним содержательным вопросом (overlap).

**Сделано:**
- `dialogue_slots`: `_slot_turn_accepts` — реплика вне контракта слота → `clear_slot` (без keyword decay).
- `discourse_resolver`: после неудачного хода (`session_task.last_outcome=clarify|…`) короткая реплика → `ACTION_CORRECT`, не STAY.
- `orchestrator._assemble_brain_context`: прокидывает `session_task` в discourse (метаданные, не regex).
- `core/turn_reconcile.py`: единая сверка слотов на каждом plan() + `active_dialogue_slot_kind` для footer.
- `reply_mode_footer` / `input_layer`: footer читает reconciled kind, не залипший persisted.
- `pipeline.call_brain` + `resolve_brain_route`: reconcile после async discourse; `hydrate_session_task`.
- Метрики: `dialogue_slot_cleared_total`, `dialogue_slot_cleared_<kind>`.
- VERSION → 3.5.10.
- Убраны добавленные regex (`user_correcting_bot`, расширение `_TOPIC_CHANGE_PATTERNS`, overlap-heuristic).

**Verify:** `pytest tests/test_turn_reconcile.py tests/test_dialogue_slots.py tests/test_discourse_resolver.py -q`; `release_guard --smoke`.

**Deploy:** не выкатывали.

---

## 2026-06-13 — Документация: кэш, задержки, синхронизация docs

**Контекст:** снят prod-снимок cache/latency (VPS 24h); нужен runbook и порядок в индексах.

**Сделано:**
- `scripts/snapshot_cache_latency.py` — llm_usage + turns + metrics_timeseries + stage_ms
- `docs/CACHE_LATENCY_METRICS_RU.md` / `.md` — runbook
- `docs/archive/CACHE_LATENCY_SNAPSHOT_2026-06-13_RU.md` — снимок без PII
- Обновлены: `docs/README.md`, `index.md`, `index.ru.md`, `REPO_MAP.md`, `scripts-cli.md`, `admin-ops*`, `PRODUCTION_EVIDENCE_REPORT*`, `VERSION` → 3.5.9, счётчики тестов (430 файлов / 2718+ cases)

**Verify:** `python -m py_compile scripts/snapshot_cache_latency.py`; smoke на VPS.

---

## 2026-06-13 — user_facts identity recall + CI (discourse context)

**Контекст:** prod — `/me` знает имя, brain на «как меня зовут?» отвечал «не знаю»; CI красный после identity-теста.

**Сделано:**
- `detect_pre_llm_shortcut` → `user_facts_identity`; `pre_llm_plan` direct reply
- `brain_user_facts_from_store` в orchestrator brain context (DM aggregate)
- `discourse_resolver._publish_discourse_context` — флаги short-circuit не теряются на shallow copy context
- Privacy: тест без реального telegram_id
- `pyproject.toml`: `reportUnsupportedDunderAll = false`

**Verify:** `pytest tests/test_user_facts_identity_recall.py tests/test_discourse_resolver.py tests/test_brain_operational_short_circuit_meta.py -q`; `check_public_privacy --ci`; `release_guard --smoke`.

**Deploy:** VPS `8de96c7` via `gemma_panel.sh update`.

---

## 2026-06-13 — Backfill DAILY_OPS 05–13 июня (VPS_PROD)

**Контекст:** суточные `docs/archive/DAILY_OPS_*` не генерировались с 05.06; данные в turns/llm_usage на VPS были.

**Сделано:** `--date` в `server_full_audit.py`; `--backfill-from/to` в `daily_server_digest.py`; backfill 9 дней на VPS.

**Verify:** `docs/archive/DAILY_OPS_2026-06-05_RU.md` … `2026-06-13`; 10–12 июня — 0 turns.

---

## 2026-06-13 — Discourse resolver: единая нить диалога до routing (IUR + thread judge)

**Контекст:** prod — эллипсис «как бы ты сейчас назвал правильно» после разговора про ИИ → `intent=general`, ответ про траву/Иран (context drift). Повторяющийся класс багов из gemma_bot_v2 (article_thread, user_facing_contract patches).

**Решение (архитектура, не keyword-списки):**
- `core/brain/discourse_resolver.py` — единая точка **до** intent/router: structural continuation (DSV, expects_reply, registry heuristics), IUR-lite rewrite, correction/tone, batch guard.
- `core/brain/discourse_thread_judge.py` — опциональный LLM judge (stay/branch/correct) на пограничных `structural`; upgrade в `apply_discourse_to_context_async` после sync в `orchestrator.plan()`.
- Интеграция: `orchestrator.plan` (sync), `pipeline.call_brain` + `resolve_brain_route` (async + judge), `profile_registry` continuation inherit, `dialogue_context` DSV (`last_intent`, `last_profile`).
- Prompt: модуль `active_thread` в `prompt_modules.py`; hygiene — `deprioritize_failed_dialogue_rows` в `context_compression` / `behavior_store`.
- Audit: `discourse` в `build_route_audit` → `turn_observer` (`discourse_action`, `discourse_reason`, …).

**Verify:** `pytest tests/test_discourse_resolver.py tests/test_route_audit_and_context.py tests/test_profile_continuation.py` (targeted); полный CI локально не гоняли.

**Deploy:** только `bash scripts/gemma_panel.sh update` на VPS после merge.

---

## 2026-06-13 — Audit fixes: Qdrant fail-fast, polling webhook, healers, Docker

**Контекст:** независимый code-review (Qdrant fail-open, polling 409, heal allowlist, Dockerfile).

### Сделано

- `core/qdrant_startup.py` + `QDRANT_STARTUP_STRICT` (default true)
- `input_layer.start_polling` → `delete_webhook` перед polling
- `HEAVY_MODULES_UNDER_PRESSURE` в heal env allowlist; sanitize module names
- `MaintenanceBridge` / `AutoHostPressureHealer` — warning вместо silent debug
- Dockerfile: multi-stage debian, USER 1000, chown `/app/data`

### Verify

```bash
python -m pytest tests/test_qdrant_startup.py tests/test_telegram_polling.py tests/test_heal_executor.py tests/test_event_healers.py -q
python scripts/release_guard.py --smoke
```

Полный pytest не гоняли.

---

## 2026-06-13 — CodeQL wave 3: 7 open alerts (#41–#120)

**Контекст:** после wave 2 на GitHub остались 7 High alerts (clear-text logging/storage).

### Сделано

- Typed JSON writers вместо `write_public_json_file` + callback (CodeQL не доверял sanitizer arg)
- `audit_host_public`: убраны `samples_*`, errors без raw `top` tuples
- `render_audit_document_md`, `audit_summary_log_line`, `scan_summary_log_line`, `security_audit_public_json_text`
- CodeQL extension pack `gemma-agent/python-extensions` + `codeql-config.yml`
- Скрипты: `server_full_audit`, `daily_server_digest`, `scan_archive_leaks`, `agent_security_audit`

### Verify

```bash
python -m pytest tests/test_sensitive_export.py -q
```

**Не гоняли:** полный pytest; CodeQL на GitHub (нужен push).

### Follow-up (тот же день, вечер)

- CodeQL workflow **упал**: `gemma-agent/python-extensions not found in registry` — убран `config-file` с unpublished pack
- После успешного CodeQL остались **4 новых** alert (#121–#124) — storage/logging на `write_*` и `security_audit_public_json_text`
- **Fix:** counts-only JSON/MD (`audit_document_counts_payload`), `security_audit_stdout_json` (literal keys + bools), pack → `gemma_agent-python`
- **Итог:** `60c5119` — **0 open CodeQL alerts**, CI + release-guard + CodeQL green

---

## 2026-06-13 — Security wave 2: CodeQL sanitization + workflow

**Контекст:** на GitHub 27 open CodeQL alerts; Dependabot alerts пусто; pip-audit — 2 ignored CVE в aiohttp (aiogram pin).

### Сделано

- `write_public_json_file`, `build_heuristic_miss_row` в `sensitive_export.py`
- Mem0 connectivity: без raw HTTP body в `user_message`; startup logs через `mem0_log_facets`
- Audit scripts: запись только через sanitizer; probe scripts — len без контента
- `.github/workflows/codeql.yml` — перескан после push
- `docs/SECURITY_GITHUB_FINDINGS_RU.md`

### Verify

```bash
python -m pytest tests/test_sensitive_export.py tests/test_heuristic_misses_log.py tests/test_news_disclaimer.py -q
python -m pip_audit -r requirements.txt --ignore-vuln CVE-2026-34993 --ignore-vuln CVE-2026-47265
```

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
- **CI follow-up (2026-06-13):** `test_dialogue_slots` — `recent_messages` в fixture; `test_news_consistency` — `asyncio.run` вместо `@pytest.mark.asyncio` (нет плагина в CI).

### Deploy (бот)
- После pull на VPS: перезапуск бота (`gemma_panel.sh`); YAML кеш `token_efficiency` ~30 с
- Смотреть: `[brain] prompt_metrics`, `context_hard_limit_enforced_total`

### Документация (эта сессия)

- `docs/CONTEXT_BUDGET_GUIDE_RU.md` — runbook
- `CHANGELOG.md`, `PRODUCTION_EVIDENCE_REPORT*.md`, `ARCHITECTURE.md`, `docs/README.md`

---

## 2026-06-13 — P1/P2 hardening batch (v3.5.6)

Задачи 3–6, 7–12 из security backlog:
- PolicyEngine memory leak, API 501 stubs, timing-safe tokens, plugin hot_install lock
- USD daily budget, LLM concurrency cap, circuit breaker, request_id tracing
- DB health 503, docker api healthcheck

Не в scope этой сессии: dormant modules refactor (#13), symlink paths (#14), timezone models (#15), TypedDict sweep (#16), ADR (#17) — pip-audit уже в CI (#18).

### Verify
```bash
python -m pytest tests/test_api_http_guards.py tests/test_openrouter_circuit_integration.py tests/test_resilience_cost_guards.py -q
```

### Deploy (API)
```bash
git pull && ./scripts/gemma_api.sh restart
curl -sS http://127.0.0.1:8000/api/v1/health
```

---

## 2026-06-13 — API message/body limits (v3.5.5)

### Проблема
- HTTP API принимал `message` без лимита (100KB+ → OOM/огромные токены).
- Обход через `bot-relay` `meta`, `ops/probe`, отсутствие лимита HTTP body.

### Решение
- `core/api_request_limits.py` — единый источник лимитов из `.env`:
  - `API_MESSAGE_MAX_CHARS` (default 10000)
  - `API_RELAY_META_MAX_JSON_CHARS` (default 4096)
  - `API_MAX_REQUEST_BODY_BYTES` (default 65536)
- Модели: `ChatRequest`, `BotRelayRequest`, `OpsProbeRequest`.
- Middleware 413 до парсинга handler.

### Verify
```bash
python -m pytest tests/test_api_request_models.py tests/test_api_request_limits.py -q
python scripts/release_guard.py --smoke
```

---

## 2026-06-13 — API_TOKEN guard + ops stdout (v3.5.4)

### Проблема
- Placeholder `API_TOKEN` из `.env.example` мог остаться в production при включённом HTTP API.
- Ops-скрипты печатали excerpts user-текста / полный connectivity report в stdout.

### Решение
- `core/api_auth.py`: `enforce_startup_api_token_config()` — `SystemExit` если token default и (`APP_ENV=production|prod` **или** `API_ENABLED=true`).
- `api.py`: guard до инициализации модулей.
- `scripts/agent_security_audit.py`: fail при `API_ENABLED` + default token.
- `day_conversation_audit.py`: `last_user_len` вместо excerpt.
- `check_connectivity.py` + `connectivity_report_public()`: только ok/error_code/http_status.

### Verify
```bash
python -m pytest tests/test_api_auth.py -q
python scripts/release_guard.py --smoke
```

### Deploy (API)
- VPS: задать сильный `API_TOKEN`, `APP_ENV=production`, перезапуск `gemma_api.sh` / panel.

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
