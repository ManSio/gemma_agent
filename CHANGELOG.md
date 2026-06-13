## [2026-06-13] — v3.5.21: article_thread opinion/clarify (no search fallback)

- **Fix:** «как ты думаешь правда?» / «я про статью» после paste — brain + slot hint, не search `__fallback__`.
- **Split:** search follow-up (`что ещё известно`) vs opinion/clarify brain follow-up.

### Verify
```bash
python -m pytest tests/test_article_thread_followup.py tests/test_dialogue_slots.py -q
```

---

## [2026-06-13] — v3.5.20: hot_path slim shared guards

- **Fix:** `brain_hot_path_slim_eligible` now rejects thread URLs (`urls_chron`), Telegram reply threads, and document intake (same as chat_context_slim).
- **Refactor:** `_brain_slim_shared_rejects` — single guard path for both slim modes.

### Verify
```bash
python -m pytest tests/test_brain_hot_path_slim.py tests/test_brain_chat_context_slim.py -q
```

---

## [2026-06-13] — v3.5.19: news dialogue consistency check (P3)

- **Wired:** `NewsConsistencyChecker.check_dialogue_consistency` on news returns via `_return_news_with_telemetry` (log-only, does not block reply).
- **Log:** `consistency_checked/ok/conflicts_count/recommendation` in `news_generation_log`.
- **Env:** `NEWS_CONSISTENCY_CHECK_ENABLED` (default true).

### Verify
```bash
python -m pytest tests/test_news_hot_path_wiring.py tests/test_news_consistency.py -q
python scripts/release_guard.py --smoke
```

---

## [2026-06-13] — v3.5.18: news narrative self-verify with source_context (P2)

- **Wired:** `_llm_digest_narrative_brief` → `run_self_verify(source_context=...)`; результат в `news_generation_log`.
- **Env:** `NEWS_SELF_VERIFY_ENABLED` (default true).

### Verify
```bash
python -m pytest tests/test_news_hot_path_wiring.py -q
python scripts/release_guard.py --smoke
```

---

## [2026-06-13] — v3.5.17: news hot path wiring (sources, validator, llm_usage log)

- **Wired:** `try_news_reply` / `compose_news_digest_from_search` / item & web digest — `NewsSource` + disclaimer + `news_generation_log` → `llm_usage.jsonl`.
- **Wired:** `_fetch_page_article` — `NewsValidator.validate_fetch()` (HTTP, captcha/cloudflare, confidence).
- **Added:** `tests/test_news_hot_path_wiring.py`.

### Verify
```bash
python -m pytest tests/test_news_hot_path_wiring.py tests/test_news_reply.py -q
python scripts/release_guard.py --smoke
```

---

## [2026-06-13] — CI + CodeQL wave 4 (regex_safe, daily digest)

- **Fix:** CI `test_finalize_send_path_inventory` — проверка полного тела `_send_output`.
- **Added:** `core/regex_safe.py` — cap user input before regex (ReDoS guard).
- **Fix:** CodeQL `py/polynomial-redos` — bounded patterns + `safe_re_*` на flagged sites.
- **Fix:** CodeQL `py/clear-text-storage` — `write_daily_ops_md` в `daily_server_digest.py`.
- **Env:** `REGEX_INPUT_MAX_LEN`.

### Verify
```bash
python -m pytest tests/test_finalize_send_path_inventory.py tests/test_regex_safe.py -q
python scripts/release_guard.py --smoke
```

---

## [2026-06-13] — v3.5.16: outbound thread guard + intent from TurnMeaning + audit emit

- **Added:** `core/outbound_thread_guard.py` — pre_send: блок agent-meta на immediate followup активной нити.
- **Wired:** `scenario_engine.apply_pre_send` — `thread_guard` recover без regen LLM.
- **Added:** `intent_hint_from_turn_meaning` в planner (`_detect_intent`) до keyword heuristics.
- **Fix:** `turn_meaning_audit_for_emit` — fallback из `turn_meaning` / `turn_state_audit` / plan context.
- **Wired:** discourse/turn_meaning в `output.meta` для pre_send (`chat_orchestrator`, `input_layer`).
- **Env:** `OUTBOUND_THREAD_GUARD_ENABLED`, `OUTBOUND_THREAD_MIN_TOKEN_OVERLAP`.

### Verify
```bash
python -m pytest tests/test_outbound_thread_guard.py tests/test_turn_decision_spine.py tests/test_turn_reconcile.py -q
```

---

## [2026-06-13] — v3.5.15: turn decision spine (post-reconcile glue)

- **Added:** `core/turn_decision_spine.py` — после meaning/discourse/collapse: ephemeral + `meaning_profile_lock`.
- **Added:** `core/discourse_thread_contract.py` — публичный API нити (без private imports).
- **Fix:** orchestrator не кэширует ephemeral до reconcile; `build_operator_corrections_hint` всегда через spine/contract.
- **Fix:** `apply_meaning_profile_lock` после classifier/continuation (agent → standard не затирается).
- **Added:** `deactivate_legacy_generic_rating_lessons()` + `scripts/deactivate_legacy_ephemeral_lessons.py`.

### Verify
```bash
python -m pytest tests/test_turn_decision_spine.py tests/test_feedback_contract.py tests/test_turn_meaning.py -q
python scripts/deactivate_legacy_ephemeral_lessons.py
```

---

## [2026-06-13] — v3.5.14: immediate thread followup + feedback contract + agent profile

- **Fix:** `discourse_resolver` — `immediate_thread_followup`: короткий ход после ответа = stay, если <N новых content-токенов вне last Q/A (не списки «так/это»).
- **Fix:** substantive `почему` больше не рвёт нить, когда followup по контракту; `turn_meaning` — continuation до substantive.
- **Fix:** `/new` чистит `recent_user_remarks` и `policy_slots.article_thread` (эпоха).
- **Added:** `core/feedback_contract.py` — 👎 уроки по `anchor_user_q` нити, не по эллипсису; фильтр legacy generic на immediate followup.
- **Wired:** `profile_override_from_meaning` → `pipeline_routing` (`TURN_MEANING_AGENT_PROFILE`, default `standard`).
- **Env:** `DISCOURSE_NOVEL_TOPIC_TOKENS`, `TURN_MEANING_AGENT_PROFILE`.
- **Privacy:** тесты — только фиктивные Telegram ID из `tests/fixtures/telegram_test_ids.py` (CI `check_public_privacy`).

### Verify
```bash
python -m pytest tests/test_discourse_resolver.py tests/test_turn_meaning.py tests/test_feedback_contract.py tests/test_prod_thread_wratmak.py -q
```

---

## [2026-06-13] — v3.5.13: TurnMeaning gates planner shortcuts (weather/geo/pre_llm)

- **Added:** `core/turn_shortcut_gate.py` — structural TurnMeaning до planner shortcuts; блок weather/geo при `referent=agent|user`, correction, weather-on-thread-stay без slot bind.
- **Wired:** `orchestrator.plan()` — gate `pre_llm`, `weather_direct`, `geo_nearby`, `telegram_location`, `referential_math`; ранний `turn_meaning` в `pre_ctx` без повторного structural.
- **Added:** structural referent user/agent в `turn_meaning.py` (identity markers + second-person questions).
- **Env:** `TURN_SHORTCUT_GATE_ENABLED` (default true).

### Verify
```bash
python -m pytest tests/test_turn_shortcut_gate.py tests/test_turn_meaning.py tests/test_prod_thread_wratmak.py -q
python scripts/release_guard.py --smoke
```

---

## [2026-06-13] — v3.5.12: TurnMeaning + judge bypass fix + ops hardening

- **Added:** `core/turn_meaning.py` — единый verdict хода (speech_act, referent, thread_action) до discourse/collapse; LLM judge на пограничных structural stay и вопросах в активной нити.
- **Fix:** `turn_reconcile` — brain path снова вызывает LLM judge после sync plan (`_needs_async_meaning_upgrade`), без двойного judge при `source=llm`.
- **Fix:** `discourse_resolver` — discourse читает `turn_meaning` как single source; agent-referent routing hint.
- **Fix:** `SelfHealingEngine.get_instance()` + `maintenance_tick()` для healers.
- **Fix:** `/new` (`conversation_epoch`) очищает `pending_correction`.
- **Obs:** `turn_meaning_audit` в `turn.outcome` и `turns.jsonl`.

### Verify
```bash
python -m pytest tests/test_turn_meaning.py tests/test_turn_reconcile.py tests/test_prod_thread_wratmak.py tests/test_discourse_resolver.py -q
python scripts/release_guard.py --smoke
```

---

## [2026-06-13] — v3.5.11: TurnStateVector + slot_registry (turn collapse)

- **Added:** `core/turn_state.py` — TurnStateVector: single collapsed state per turn (discourse + slots + prior outcome).
- **Added:** `core/slot_registry.py` — slot contracts `accepts_turn` per kind (weather/article/spatial).
- **Wired:** `turn_reconcile` → `collapse_turn_state`; `turn_observer` fields `slot_cleared`, `expects_correction`.
- **Docs:** `ARCHITECTURE.md` — turn state collapse diagram.

### Verify
```bash
python -m pytest tests/test_turn_state.py tests/test_turn_reconcile.py tests/test_dialogue_slots.py -q
python scripts/release_guard.py --smoke
```

---

## [2026-06-13] — v3.5.10: turn reconcile (slots + discourse), stale weather footer fix

- **Added:** `core/turn_reconcile.py` — single per-turn slot reconcile after discourse; `active_dialogue_slot_kind` for footer; metrics `dialogue_slot_cleared_*`.
- **Fix:** `dialogue_slots` slot contract — non-binding turn clears `weather_await_city` (no keyword decay lists).
- **Fix:** `discourse_resolver` — `prior_unsatisfactory` from `session_task.last_outcome` → `ACTION_CORRECT` (not facts dump on short reply).
- **Wired:** orchestrator `plan()`, `pipeline.call_brain`, `resolve_brain_route`; footer reads reconciled slot kind.

### Verify
```bash
python -m pytest tests/test_turn_reconcile.py tests/test_dialogue_slots.py tests/test_discourse_resolver.py -q
python scripts/release_guard.py --smoke
```

---

## [2026-06-13] — v3.5.9: cache/latency metrics docs, identity recall, discourse context CI

- **Added:** `scripts/snapshot_cache_latency.py` — ops snapshot (llm_usage, turns, metrics_timeseries, stage_ms).
- **Docs:** `docs/CACHE_LATENCY_METRICS_RU.md`, `CACHE_LATENCY_METRICS.md`, archive snapshot 2026-06-13; hub/index/REPO_MAP/scripts-cli/admin-ops synced.
- **Fix:** `user_facts` identity recall (`как меня зовут` / `кто я`) via pre-LLM direct plan; DM brain context uses profile aggregate.
- **Fix:** discourse resolver publishes into caller `context` dict (short-circuit flags survive).
- **CI:** privacy test id sanitized; basedpyright lazy `__all__` warning suppressed.

### Verify
```bash
python -m py_compile scripts/snapshot_cache_latency.py
python -m pytest tests/test_user_facts_identity_recall.py tests/test_discourse_resolver.py tests/test_brain_operational_short_circuit_meta.py -q
python scripts/check_public_privacy.py --ci
python scripts/release_guard.py --smoke
```

---

## [2026-06-13] — v3.5.8: discourse resolver (thread continuity before routing)

- **Added:** `core/brain/discourse_resolver.py` — structural continuation, IUR-lite rewrite, correction signals, batch guard; `discourse_thread_judge.py` for ambiguous stays.
- **Integrated:** orchestrator `plan()`, `pipeline.call_brain`, `resolve_brain_route`, `profile_registry` continuation, `prompt_modules.active_thread`.
- **Hygiene:** `deprioritize_failed_dialogue_rows` strips old LLM-error turns from recent context.
- **Observability:** `discourse` block in `router_route_audit` / `turns.jsonl`.
- **Tests:** `tests/test_discourse_resolver.py`.

## [2026-06-13] — v3.5.7: audit fixes (Qdrant startup, polling, healers, Docker)

### Исправлено
- **Qdrant startup**: `core/qdrant_startup.py` — fail-fast при недоступном API (`QDRANT_STARTUP_STRICT`, default true)
- **Telegram polling**: `delete_webhook` перед `start_polling` (409 после webhook)
- **Heal executor**: `HEAVY_MODULES_UNDER_PRESSURE` в env allowlist; валидация имён модулей
- **MaintenanceBridge**: module-level imports; ошибки tick → `warning` + traceback
- **AutoHostPressureHealer**: warning при неуспешном `apply_steps`
- **Dockerfile**: multi-stage `python:3.11-slim-bookworm`, non-root `USER gemma`, `chown` data dirs

### Добавлено
- `tests/test_qdrant_startup.py`, `tests/test_telegram_polling.py`
- тесты healers / heavy modules env

### Verify
```bash
python -m pytest tests/test_qdrant_startup.py tests/test_telegram_polling.py tests/test_heal_executor.py tests/test_event_healers.py -q
python -m py_compile core/qdrant_startup.py core/heal_executor.py core/event_healers.py main.py
python scripts/release_guard.py --smoke
```

---

## [2026-06-13] — v3.5.6: P1/P2 hardening (policy, API, LLM guards)

### Исправлено
- **P1** `PolicyEngine`: удаление пустых ключей `call_history` (`POLICY_CALL_HISTORY_RETENTION_MIN`)
- **P1** mock API `/parents/.../children`, `/schedule/...` → HTTP **501**
- **P1** `api_auth`: `hmac.compare_digest` для токенов
- **P1** `PluginRegistry.hot_install_module`: `threading.Lock` на `sys.modules`
- **P2** `COST_DAILY_USD_BUDGET` + hard stop в OpenRouter preflight
- **P2** `OPENROUTER_MAX_CONCURRENT_CALLS` semaphore
- **P2** `CircuitBreaker` для OpenRouter (`core/resilience.py`)
- **P2** correlation id: `core/request_context.py`, API middleware, orchestrator/brain
- **P2** `/api/v1/health`: проверка БД → **503** при недоступности
- **P2** docker-compose `api` service: healthcheck на `/api/v1/health`

### Добавлено
- `tests/test_api_http_guards.py`, `tests/test_openrouter_circuit_integration.py`

### Исправлено (финал)
- `openrouter_provider.generate()`: success/error ветки под `status==200`; `record_failure` на HTTP/exception
- `/health` (legacy): проверка БД → 503

### Verify
```bash
python -m pytest tests/test_api_http_guards.py tests/test_openrouter_circuit_integration.py tests/test_resilience_cost_guards.py -q
python scripts/release_guard.py --smoke
```

---

## [2026-06-13] — v3.5.5: API message/body size guards

### Исправлено
- `core/api_request_limits.py`: `API_MESSAGE_MAX_CHARS`, `API_RELAY_META_MAX_JSON_CHARS`, `API_MAX_REQUEST_BODY_BYTES` из `.env`
- `ChatRequest` / `BotRelayRequest` / `OpsProbeRequest`: `message` с `max_length` из env (дефолт 10000)
- `BotRelayRequest.meta`: лимит сериализованного JSON
- ASGI middleware `RequestBodySizeLimitMiddleware` — HTTP 413 на oversized body

### Добавлено
- `tests/test_api_request_models.py`, `tests/test_api_request_limits.py`

### Verify
```bash
python -m pytest tests/test_api_request_models.py tests/test_api_request_limits.py -q
python -m py_compile core/api_request_limits.py api.py core/api_ops.py
```

---

## [2026-06-13] — v3.5.4: API_TOKEN startup guard + ops stdout hardening

### Исправлено
- **P1 security:** `enforce_startup_api_token_config()` — отказ старта API с placeholder `API_TOKEN` при `APP_ENV=production` или `API_ENABLED=true`
- `api.py` вызывает guard до `initialize_modules()`
- `scripts/agent_security_audit.py` — проверка API_TOKEN при включённом API
- Ops: `day_conversation_audit.py` — длина последнего user-сообщения вместо excerpt; `check_connectivity.py` — public JSON без message bodies

### Добавлено
- `tests/test_api_auth.py` — 8 тестов на token guard
- `connectivity_report_public()` / `connectivity_stdout_json()` в `core/connectivity_check.py`

### Verify
```bash
python -m pytest tests/test_api_auth.py -q
python -m py_compile core/api_auth.py api.py core/connectivity_check.py
```

---

## [2026-06-13] — v3.5.3: CodeQL wave 3 — 7 remaining alerts

### Исправлено
- CodeQL #41, #49, #105, #117–#120: typed writers (`write_audit_document_json`, `write_scan_report_json`), stdout только counts (`audit_summary_log_line`, `scan_summary_log_line`), MD через `render_audit_document_md`
- `audit_host_public`: без excerpts/samples; errors — только `kinds_count`
- CodeQL model pack: `.github/codeql/extensions/gemma-agent-python/` — `barrierModel` для sanitizers (`clear-text-logging` / `clear-text-storage`)

### Verify
```bash
python -m pytest tests/test_sensitive_export.py -q
python -m py_compile core/sensitive_export.py scripts/server_full_audit.py
```

---

## [2026-06-13] — v3.5.2: CodeQL sanitization wave + security workflow

### Исправлено
- CodeQL: logging/storage — `write_public_json_file`, `build_heuristic_miss_row`, Mem0 connectivity без raw body
- Probe/audit scripts: не печатают user content в stdout
- Восстановлен `.github/workflows/codeql.yml` для пересканирования alerts

### Документация
- `docs/SECURITY_GITHUB_FINDINGS_RU.md` — статус CodeQL + pip-audit (aiohttp CVE ignored)

---

## [2026-06-13] — v3.5.1: Hard context limit 15K, CI aiohttp pin, CodeQL sanitization

### Исправлено
- **CI:** `aiohttp>=3.9.0,<3.14` — совместимость с `aiogram 3.28.2` (dependabot ломал `pip install`)
- **Hard context limit:** `enforce_context_limit()` в `core/context_collapse.py` — prune `prompt_parts` при превышении `budget_hard_limit_tokens`, даже когда `collapse.enabled=false`
- **YAML:** `compactor` вынесен из `budget`; `hard_limit_tokens: 15000`; `compactor.enabled: true`
- **CodeQL:** sanitization Mem0 paths (`mem0_path_log_facets`), heuristic_misses (`user_id_hash`), audit scripts — только public view

### Добавлено
- Тесты: `tests/test_context_hard_limit.py`, `tests/test_token_efficiency_config.py`
- Метрики: `context_hard_limit_enforced_total`, `context_hard_limit_pruned_total`

### Документация
- `docs/DEV_DIARY_RU.md` — журнал разработки (читать агенту первым при багфиксе)
- `docs/CONTEXT_BUDGET_GUIDE_RU.md` — runbook лимитов контекста

**Deploy:** после pull — рестарт бота; `python -m pytest tests/test_context_hard_limit.py tests/test_token_efficiency_config.py -q`; CI `release-guard` + `CI` на push

---

## [2026-06-08] — v3.5.0: News Reliability Hardening (source attribution + verification)

### Добавлено
- **Source Attribution Layer:** `core/news_article_model.py` — `NewsArticle`/`NewsSource` TypedDict с обязательным URL, timestamp, domain, confidence
- **Fetch Validation:** `core/news_validator.py` — `NewsValidator.validate_fetch()` проверяет HTTP status, Content-Type, Cloudflare/Captcha, длину текста; `fallback_fetch()` — regex-извлечение при пустом парсинге
- **Logging:** `core/llm_usage_store.py` — `news_generation_log()` логирует каждый новостной ответ в `llm_usage.jsonl` (sources, confidence, self_verify, fetch_methods)
- **Self-Verify с источниками:** `core/brain/self_verify_pass.py` — `run_self_verify()` принимает `source_context`; для новостей промпт проверяет что факты есть в источниках
- **Disclaimer Generator:** `core/news_disclaimer.py` — `NewsDisclaimerGenerator` выдаёт дисклеймер в зависимости от качества источников (HIGH/MEDIUM/LOW)
- **Consistency Checker:** `core/news_consistency_checker.py` — `NewsConsistencyChecker` выявляет противоречия (разные даты одного события) между turn'ами
- **Monitoring:** `core/monitoring.py` — `set_gauge()`, `observe()`/`histogram_avg()` для news-метрик (fetch success, self-verify fix rate, parsing confidence avg)
- **Tests:** 37 тестов в `tests/test_news_*.py` (source attribution, validator, disclaimer, consistency)

### Изменения
- `format_news_from_search()` и `format_news_loose_from_summary()` — добавлена поддержка `sources` параметра для дисклеймера
- Каждый новостной ответ теперь включает обязательную атрибуцию источника

### Документация
- `docs/NEWS_RELIABILITY_GUIDE.md` — runbook по мониторингу и troubleshooting
- `docs/PROD_NEWS_ALERTS.md` — production alerts и dashboard

**Deploy:** после push — `python -m pytest tests/test_news_*.py -v`; проверить `llm_usage.jsonl` на наличие записей `news_generation`

---

## [2026-06-05] — v3.4.0: spatial_design v1 (план → сверка → визуализация)

### Добавлено
- Модуль `spatial_design` v1.0.0: perceive (vision+OCR), validate (мм), бриф, обратная связь, одна генерация
- `core/spatial_design/` — classifier, guards, counts, placement_rules, scale, symbols
- Конфиг: `config/spatial_domains/` (6 доменов), `config/spatial_symbols/`
- Док: `docs/SPATIAL_DESIGN_V1_RU.md`, `docs/SPATIAL_DESIGN_USER_RU.md`
- Тесты: `test_spatial_design.py`, `test_spatial_v1_release.py`, корпус `corpus_v1.json`
- Env: `SPATIAL_DESIGN_ENABLED`, `SPATIAL_VISION_MODEL`, `SPATIAL_MIN_ELEMENT_CONFIDENCE`, `DIALOGUE_SLOT_SPATIAL_TURNS`

### Интеграция
- Orchestrator intent `spatial_design` до `image_generation`; слот `spatial_project` в `dialogue_slots`
- 66 модулей в каталоге (`spatial_design` tier B)

**Deploy:** после push — HOST_LAN + VPS_PROD; TG smoke по `SPATIAL_DESIGN_V1_RU.md`

---

## [2026-06-03] — v3.3.4: сессии image gen, pending/multiref, альбом TG

### Исправлено
- Один план + фото+подпись: фото не возвращалось в `pending_images` для следующего плана (`input_layer`, `user_image_pending`)
- Merge pending только при явном multiref (`image_gen_multiref.prose_wants_multiref_pending_merge`)
- Сброс очереди и сессии при «новый проект» / новый план (`image_gen_nl`, `image_edit_session`)
- Альбом Telegram: буфер `media_group_id` (~1.2s) перед register

### Добавлено
- `core/image_edit_session.py`, `core/telegram_media_group_buffer.py`
- Док: `docs/IMAGE_GEN_SESSIONS_RU.md`; env `IMAGE_EDIT_SESSION_TTL_SEC` в `.env.example`

### Версии
- Приложение `3.3.3` → `3.3.4`; `image_generator` `0.1.1` (логика в core, не в module.py); `bundled_with` плагинов = `3.3.4`
- `bundled_with` всех плагинов синхронизирован (`sync_versions`, `auto_version_from_commits`)

**Deploy:** после push — HOST_LAN + VPS_PROD `gemma_panel.sh update`

---

## [2026-05-30] — Аудит 3 ИИ: world_brief, UX fallbacks, docs sync

### Добавлено
- Мировой дайджест: `world_brief`, thematic SearX, parallel search, page enrich (`70a611d`)
- `llm_transient_recovery`, мягкие fallback без «модель не ответила» (`649c115`)

### Исправлено
- OpenRouter HTTP session per event loop; `FINALIZE_LEAK_STRIP_PATTERNS` (`32cebb3`)
- finalize leak-corpus, `stage_ms`/`decision_trace` (`5963cce`)
- CI: `test_telegram_output_guard` — заголовок «Главные мировые новости на …»; `test_brain_glitch_fallback` — новые llm_error fallback

### Документация
- [EXTERNAL_AI_SYNTHESIS_2026-05-30_RU.md](docs/EXTERNAL_AI_SYNTHESIS_2026-05-30_RU.md) — таблица закрыто/открыто
- PROJECT_STATUS, README docs, REFORM_S9, EXTERNAL_AI_REVIEW §8

**Deploy:** `649c115` LAN + VPS

---

## [2026-05-30] — Docs: 17 файлов → archive/, сжатый индекс

### Изменено
- Перенос в `docs/archive/`: VPS-снимки, SERVER_AUDIT 29–30.05, backlog closures, Q2 presentation
- `docs/README.md` — 4 уровня, ~50 строк вместо ~150
- `archive/README.md` — полный реестр архива
- Ссылки в живых docs → PROJECT_STATUS / archive

---

## [2026-05-30] — Документация: иерархия, актуализация README/CHANGELOG

### Изменено
- `docs/README.md` — уровни 0–3 (старт / роль / справочник / архив)
- `docs/PROJECT_STATUS_2026-05-28_RU.md` — сводка 30.05, git `5aea4e1`, P0–P3, news, `/admin_self`
- `docs/DOC_SNAPSHOT_RU.md` — **2326** тестов, 67 anti-regression
- `README.md`, `README.ru.md` — короткий вход, ссылки на уровень 0
- `docs/AI_HANDOFF_BRIEF_RU.md` — pre_llm_plan, P3, env

---

## [2026-05-30] — Новости search-only, `/admin_self`, LLM digest

### Добавлено
- Search-only news digest, фильтр portal junk, deep follow-up
- `/admin_self` — метрики 24h, KV, p95, C6 recent (`core/admin_self_status.py`)

### Исправлено
- LLM digest при search-only path; дубликаты в item pick

### Коммиты
- `3d5c1cf` … `5aea4e1`

---

## [2026-05-30] — P3: pre-LLM plan (wall_clock до brain)

### Добавлено
- `core/pre_llm_plan.py`, `try_wall_clock_direct_reply` в orchestrator.plan
- Метрика `pre_llm_plan_direct_total`; env `PRE_LLM_PLAN_ENABLED=true`

### Исправлено
- Regex «Который **сейчас** час?» в `timezone_inference.py`

### Тесты
- `tests/test_pre_llm_plan.py`, `tests/test_wall_clock_intent.py`

---

## [2026-05-30] — Аудит P0/P1/P2: Goal Runner, Mem0, healers, память

### Исправлено
- Goal Runner: `EXECUTOR_MODE` не форсит AUTO_START
- Mem0 singleton в pipeline; healers + `plugin_registry`
- Affirmative «да», paste/Habr, math gate, «сценарию» commerce false positive
- P1: `BRAIN_LIGHT_RECENT_COUNT`, epoch bump на pivot; P2: experience hint, probes без «не rss»

### Тесты
- `tests/test_audit_p0_fixes.py`; reform route-only **6/6**, chain **7/7**

### Документация
- `docs/DEV_DIARY_RU.md`, `docs/SERVER_AUDIT_2026-05-30_RU.md`

---

## [hotfix 2026-05-29] — §9: новости «4», «да»→поиск, погода Минск, утечки persona

### Исправлено
- Сохранение `last_news_digest_items` после LLM-дайджеста; отличие дайджеста от нумерованного ответа (пентеракт)
- «да» после вопроса про новости/Хлестова → поиск, не facts idle «Запомнил»
- wttr.in для Минска: латинский slug + retry при неверном геокоде (Dinarovka)
- Утечки `_length` / `response_tone` в ответе пользователю

### Метрики
- `brain_affirmative_search_short_circuit_total`

### Документация
- `docs/DEV_DIARY_RU.md`, `docs/SERVER_AUDIT_2026-05-29_RU.md`

---

## [docs 2026-05-23] — Актуализация документации

### Добавлено
- `docs/DOC_SNAPSHOT_RU.md` — единый снимок (версия, тесты, размеры кода, метрики, бэклог)
- `docs/AI_HANDOFF_BRIEF_RU.md` — бриф для другого ИИ-чата / агента

### Обновлено
- `docs/README.md`, `README.md`, `README.ru.md` — ~1935 тестов, ссылки на снимок и бриф
- `docs/AUDIT_ROADMAP_RU.md`, `docs/AUDIT_PROGRESS_RU.md` — устаревшие цифры и статус деплоя

---

## [hotfix 2026-05-20] — Serial pipeline в личке, KV profile sticky

### Исправлено
- **Регрессия `bf95c38`:** в private снова последовательная обработка (`Lock`); `TELEGRAM_PIPELINE_PRIVATE_PARALLEL` (default 1) — иначе гонка `recent_messages` и ответы «на прошлый вопрос»
- **KV:** `BRAIN_KV_PROFILE_STICKY` — стабильный суффикс профиля в `session_id` при скачках роутера
- **Router bypass:** не `short` на «кто создал», «почему» без `?`
- **`AgentSelfTools.self_status`:** реальный uptime и число tools

### Документация
- `docs/INCIDENT_2026-05-20_PIPELINE_PARALLEL_RU.md`, запись в `docs/DEV_DIARY_RU.md`
- Правило Cursor: `.cursor/rules/post-incident-and-deploy.mdc`

### Тесты
- `tests/test_pipeline_chat_lock.py`, `tests/test_kv_profile_sticky.py`

### Env
- `TELEGRAM_PIPELINE_PRIVATE_PARALLEL=1`, `BRAIN_KV_PROFILE_STICKY=true` — см. `.env.example`

---

## [v3.3.3] — Сценарный контур и quality gate (2026-05-19)

### Добавлено
- **`core/scenario_engine.py`** — прогноз рисков до ответа (pre_plan), финализация после execute (post_execute), проверка перед Telegram (pre_send)
- **`core/situation_playbook.py`** — справочник ситуаций → brain profile (`translation`, `math_solve`, `code_generation`, …) + подсказки в промпт
- **`core/telegram_output_guard.py`** — dedupe двух ответов, dedupe повторного фото, прямой ответ новостей из поиска
- **`core/scenario_memory.py`** — повтор сценария N раз → эфемерный урок
- Событие **`turn.scenario`** и поле `scenario_hits` в журнале ходов
- Документация: `docs/SCENARIO_ENGINE_RU.md`, `docs/AUDIT_ROADMAP_RU.md`, `docs/DEV_DIARY_RU.md`

### Исправлено
- Два несвязанных ответа на один вопрос (dedupe по ключевым словам запроса)
- Дубль «не распознан» на одно фото (окно `TELEGRAM_PHOTO_DEDUP_SEC`)
- Галлюцинации в новостях (`BRAIN_NEWS_DIRECT_FROM_SEARCH`, обрезка списка)
- Расширен anti-intrusion (ложные reminder/«ничего уточнить»)

### Env
- `SCENARIO_ENGINE_ENABLED`, `SCENARIO_MEMORY_*`, `TELEGRAM_OUTPUT_DEDUPE_*`, `TELEGRAM_PHOTO_DEDUP_*`, `BRAIN_NEWS_DIRECT_FROM_SEARCH`, `NEWS_*_MAX_ITEMS` — см. `.env.example`

### Тесты
- `tests/test_scenario_engine.py`, `tests/test_situation_playbook.py`, `tests/test_telegram_output_guard.py`

---

## [v3.3.2] — Напоминания по TZ, переключатель навыков, детект перевода (2026-05-18)

### Добавлено
- **Напоминания:** `core/reminder_dispatch.py` — парсинг «сегодня в ЧЧ:ММ», «через N мин/час», суффикс `utc`; пояс из `user_facts` или `REMINDER_DEFAULT_TIMEZONE` (default `Europe/Moscow`); перепланирование таймеров после рестарта (`register_reminder_bot`, `reschedule_pending_soon_wakes`)
- **`/admin_toggle_skill <name> on|off`** — явное включение/выключение; без аргумента — toggle; неизвестный навык → список доступных (`modules/skills/registry.py`, `skill_registry_enabled.json`)
- **Детект перевода:** `core/brain/translation_path.py` + расширенный `detect_skill_intent` в `modules/skills/router.py` (языковые шаблоны `на английский`, `english:`)

### Исправлено
- `modules/light_reminders` — делегирует хранение/доставку в `core/reminder_dispatch` (единый JSON `data/runtime/light_reminders.json`)
- `core/schedule_module.py` — согласованное добавление событий с напоминаниями

### Env
- `REMINDER_NL_ENABLED`, `REMINDER_DEFAULT_TIMEZONE`, `REMINDER_SOON_WAKE_MAX_SEC`, `REMINDER_STALE_DAYS` — см. `.env.example`

### Тесты
- `tests/test_reminder_tz.py`, `tests/test_skill_translator_detect.py`, расширен `tests/test_skill_toggle_args.py`
- **Итого:** 1711 passed, 3 skipped (полный `pytest`)

---

## [v3.3.1] — Исправлен перевод и утечки промпта (2026-05-18)

### Исправлено
- **Перевод:** отдельный fast-path `brain_translation_reply` — без tools, skills, уроков и persona (убирало обращение по имени вместо English)
- Фильтр утечек: списки tools, «Системное сообщение…», «Примечание: … TOOL_CALL»

---

## [v3.3.0] — Полный контур самообучения без заглушек (2026-05-18)

### Добавлено
- **`/rate +1|-1`** и **`/correct`** (`core/user_response_feedback.py`): оценка последнего хода → experience, CDC/reputation, уроки, ephemeral
- **👍/👎** используют тот же контур (не только сдвиг score уроков)
- **`config/heuristic_fixes.json`** + `core/heuristic_fixes.py` — жёсткие подсказки в pipeline (валюта, новости, погода, время)
- **`core/experience_rules.py`** — правила из статистики ok в experience_digest → ephemeral lessons
- **`core/learning_maintenance.py`** — цикл каждые 6ч: кластеры route_risk, auto-уроки, experience rules, снимок v_c
- **`core/learning_stagnation.py`** — детекция стагнации avg v_c по скиллам
- **`/admin_run_learning`** — принудительный запуск цикла обучения
- `session_task`: `last_skill`, `last_assistant_excerpt` для обратной связи

### Env
- `LEARNING_MAINTENANCE_ENABLED=true` (default)
- `LEARNING_MAINTENANCE_INTERVAL_SEC=21600` (6h)
- `ROUTE_RISK_CLUSTER_AUTO_LESSON=true` — авто-уроки из кластеров
- `EXPERIENCE_RULES_ENABLED=true`, `HEURISTIC_FIXES_ENABLED=true`

---

## [v3.2.0] — Надёжные ответы, напоминания, репутация скиллов, справка админа (2026-05-18)

### Добавлено
- **Новости без галлюнинаций** (`core/brain/pipeline.py`): профиль `news_brief` / `is_news` → `UniversalSearch` в `external_hint`; при пустой выдаче — явный запрет выдумывать заголовки
- **Напоминания end-to-end** (`core/reminder_dispatch.py`): `Schedule.add_event` → `light_reminders.json`; фоновая доставка в Telegram из `autopilot_cycle`
- **Финальная очистка ответа** (`core/brain/response_finalize.py`): CoT, tool markup, JSON-мусор, утечки `last_operation` / `_text=` — в конце pipeline и в `input_layer._send_output`
- **Репутация по скиллам (CDC)** (`core/cdc/engine.py`): `reputation_skill` / `cdc_agg_skill`, ключ `user_id|skill_name`; передача `skill_name` из orchestrator
- **`/admin_reputation`**: `reputation_routes` + `reputation_skills` с `confidence` (v_c), `summary.top_skills` / `weak_skills` (`core/admin_reputation_view.py`)
- **Дайджест обучения** (`core/learning_digest.py`): `/admin_learning_digest` — уроки, experience, route_risk, топ скиллов
- **Кластеры route_risk** (`core/route_risk_cluster.py`): `/admin_route_risk_clusters`; авто-уроки при `ROUTE_RISK_CLUSTER_AUTO_LESSON=true`
- **Справка и панель**: синхронизация admin-команд с хендлерами (`core/help_catalog_sync.py`); раздел «Статистика» в `/help` с кнопками `hs:`; кнопки ⭐ Репутация / 🧠 Обучение в `/admin`

### Исправлено
- **MCE experiments**: порог отката `MCE_META_ROLLBACK_DISABLE_MIN` (default 4); повторное включение при отсутствии rollback в окне
- **`/admin_reputation_reset`**: поддержка сброса скилла (`user|skill_name`) отдельно от маршрута

### Тесты
- `tests/test_response_finalize_reminders.py`, `tests/test_admin_reputation_view.py`, `tests/test_learning_digest_cluster.py`

---

## [SKILLS] — 33 domain‑specific skills + замкнутая петля обучения (2026-05-18)

### Добавлено
- **33 аналитических навыков** вместо 18 заглушек (`modules/skills/builtin_skills.py`):
  - Унаследованные: Translator, Teacher, Lawyer, DevOpsAssistant, Doctor, FinanceHelper, Psychologist, NewsAnalyst, Coder, TutorAssistant, ScheduleHelper, WeatherForecast, ShoppingList, RecipeAssistant, TravelGuide, FitnessCoach, FilmCritic, GameMaster
  - Новые 15: **MathReasoning, PhysicsEngineer, GeographyTravel, HistoryCulture, LiteratureArt, BiologyNature, TechGadgets, CareerHR, HomeDIY, SportsFitness, Gaming, BusinessEntrepreneur, CryptoInvest, AutoVehicle, ShoppingDeals**
- **Skill routing** (`modules/skills/router.py`): расширен `detect_skill_intent()` на все 33 навыка
- **Skill learning loop** — `skill_name` теперь передаётся в:
  - `core/strategy_path_memory.py` — запись в `append_strategy_success()`
  - `core/route_risk_memory.py` — запись в `record_stumble()`
  - `core/experience_memory.py` — запись в `append_success()` / `append_experience_record()`
  - `core/orchestrator.py` — извлечение `_skill_name` из `context["_skill_name"]` (заполняется в `pipeline.py`)
- **Parallel batch detector** — `is_parallel_eligible()` в `core/batch_processor.py`:
  - `_INTERNAL_DEPENDENCY_MARKERS`: зависимые выражения внутри пункта («на основе», «following»)
  - `_CROSS_REF_MARKERS`: местоимения / указатели на другие пункты («его», «этот», «данный», «вышеуказан»)
  - `_COMPARATIVE_ROOTS`: сравнение между пунктами («сравн», «разниц», «в отличие»)
  - `_DEICTIC_STARTS`: начало с отсылки («а теперь», «а что»)
  - `_has_dependency_within_item()`, `_has_cross_reference()`, `_starts_with_pronoun()` — вспомогательные функции
### Исправлено
- `core/schedule_module.py` — `add_event()` принимает `event` как строку (LLM передаёт строку, не dict)
- `core/llm_triage.py` — `run_triage_now()` теперь async-safe: разделён на `run_triage_now_async()` + sync-обёртка через `asyncio.run()`
- `core/input_handlers/commands_admin.py` — `/admin_bug_heal_triage` вызывает `await run_triage_now_async()` вместо sync `run_triage_now()`
- `core/route_risk_memory.py` — deduplication только для `clarify` (не для `fallback`)
- `core/strategy_path_memory.py` — fixed `UnboundLocalError` (store перед использованием)
- `core/user_bug_report.py` — fixed imports (`sanitize_html` → `core.telegram_util`, `admin_user_ids` → `core.admin_module`)
- `core/brain/text_helpers.py`:
  - `_user_text_looks_like_code()` — только трипл-бектики + multi-line
  - `_looks_like_garbage_json()` — не срабатывает на короткие массивы без кириллицы
- `core/meta_cognitive_engine.py` — `suggest_faster_model()` не рекомендует ту же модель; `healer_flood` threshold 30.0

### Документация
- `CHANGELOG.md` — полная история изменений

### Разное
- Очищен корень проекта от мусора (`api.py`, `_analyze_all.py`, `test_modules.py`, `verify_structure*.py`)
- Логи перемещены в `logs/`

### Тесты
- `tests/test_experience_memory.py` — +обновлены все вызовы под new skill_name signature
- `tests/test_batch_processor.py` — +12 тестов на cross-reference, comparative, deictic
- `tests/test_meta_cognitive_engine.py` — healer_flood 35, p95=46000
- **Итого (на момент релиза skills):** 1659 passed, 3 skipped

---

## [BATCH] — Исправления маршрутизации и качества batch (2026-05-18)

### Исправлено
- **Единая задача с подпунктами** (тессеракт, тест с преамбулой) больше не уходит в parallel batch — профиль `reasoning`, `is_unified_problem()` в `batch_continuation.py`
- **`extract_items`**: только нумерованные вопросы + общая преамбула к каждому пункту (преамбула не считается отдельным пунктом)
- **Утечка геолокации**: `city`/`country` не подмешиваются к абстрактным/научным пунктам batch
- **Кэш LLM**: вызовы с тегом `batch_parallel_item` не читаются из step-cache (`llm_tiered.py`) — устранены мгновенные «48/48 за 24 ms» с устаревшими ответами
- **Поправки пользователя**: «задача не решена», «неправильно» → reasoning loop; усилен блок correction в pipeline (перерешить задачу, не выводить внутренние поля)

### Тесты
- `tests/test_batch_continuation.py` — разбор тессеракта, unified problem, `_detect_batch`
- `tests/test_batch_processor.py` — фильтр user_facts по типу пункта

### Документация
- `docs/AGENT_SELF_IMPROVEMENT_RU.md`, `docs/BRAIN_AND_OPERATORS_RU.md`, `docs/LLM_CACHE.md`, `docs/README.md`, README — актуализированы

---

## [GUARD] — Dangerous Command Guard + Context protect_last_n

### Добавлено
- `core/dangerous_command_guard.py` — детектор опасных shell-команд с подтверждением
- `core/tools.py` — интеграция guard в единственную точку входа `run_tool()`

### Режимы guard (`DANGEROUS_COMMAND_MODE`)
| Режим | Поведение |
|-------|-----------|
| `off` | Guard выключен |
| `log` | Логировать подозрительные вызовы, НЕ блокировать (default) |
| `block` | Блокировать + возвращать `guard_blocked` в ответе |

### Логика блокировки
- Инструменты с префиксами `SelfDeployment.`, `VoiceModule.` (настраивается через `DANGEROUS_TOOL_PREFIXES`)
- Shell-метасимволы в аргументах (любой инструмент): `; | & $ \` \$\()
- Чувствительные пути в аргументах опасных инструментов: `/etc/, /proc/, /sys/, ~/.ssh, /root/` (траверсал `../../etc/` тоже ловится)
- Allowlist `DANGEROUS_COMMAND_ALLOWLIST` — явное разрешение для block-режима

### Context Compression: protect_last_n
- `core/context_compression.py:compress_recent_dialogue()` — последние N сообщений (`CONTEXT_PROTECT_LAST_N`, default `2`) проходят **без обрезки**
- `core/compactor.py:compact_dialogue_llm()` — новая сигнатура `(str, list[dict])`: LLM сжимает только сообщения старше protect_last_n, свежие приклеиваются verbatim
- `core/compactor.py:COMPACTOR_VERSION` → `1.1.0`
- `core/brain/pipeline.py` — обновлён вызов compactor под новую сигнатуру; **при отказе LLM recent_dialogue не теряется**
- `COMPACTOR_PROTECT_LAST_N` (env, default `2`)
- `CONTEXT_PROTECT_LAST_N` (env, default `2`)

### Тесты
- `tests/test_compactor.py` — +6 тестов на protect_last_n и protected_messages (18 всего, все проходят)
- `tests/test_context_compression.py` — +4 теста на protect_last_n (5 всего, все проходят)
- Integration: 5 ручных тестов guard (name-block, path, traversal, safe, injection)

---

## [MCE] — Meta-Cognitive Engine: замкнутая петля управления

### Добавлено
- `core/meta_cognitive_engine.py` — автоприменение рекомендаций к `.env` (MCE_AUTO_APPLY)
- `docs/MCE_RU.md` — полная документация MCE на русском

### Механизм
- `_set_env()` — запись в `os.environ` + `.env` файл в рантайме
- `_auto_apply_recommendations()` — безопасные рекомендации применяются при confidence > 0.5
- `_apply_experiment_outcome()` — эксперименты меняют env при promote/rollback
- Env-реакция на критические дрейфы (confidence dropping → reasoning gate, latency → сброс порога)
- `MCE_AUTO_APPLY=true`, `MCE_AUTO_APPLY_MIN_CONFIDENCE=0.5`

### Исправлено
- `core/autopilot_cycle.py` — `maintenance.tick` теперь эмитится на каждый внутренний цикл (каждые ~180с вместо раз в 20 минут)
- `SELF_LEARNING_ENABLED=true` — уроки снова накапливаются
- `/note` — полноценный обработчик: сохраняет заметки в `data/runtime/user_notes.jsonl`

### Документация
- `docs/MCE_RU.md` — описание архитектуры, компонентов, env-переменных и админ-команд
- `README.md`, `README.ru.md`, `docs/README.md` — ссылки на MCE в стеке автономии

---

## [BATCH] — Parallel Batch Processor

### Добавлено
- `core/batch_processor.py` — асинхронный parallel batch engine
- `tests/test_batch_processor.py` — 21 тест

### Механизм
- Адаптивный scheduler: старт с 2 параллельных, creep до 12 (подстраивается под API rate limits)
- `asyncio.Semaphore` + backoff при 429 rate limit
- `is_parallel_eligible()` — отсев зависимых пунктов (нельзя параллелить если есть кросс-референсы)
- `return_exceptions=True` — ошибка одного пункта не ломает остальные
- Интеграция в `call_brain()` при `profile=batch`, fallback на sequential при ошибках
- User facts read-only, минимальный контекст для сабвызовов (cheap-модель)

### Конфигурация
- `BATCH_PARALLEL_INITIAL=2`, `BATCH_PARALLEL_MAX_CAP=12`, `BATCH_PARALLEL_MIN_ITEMS=3`
- `BATCH_PARALLEL_MAX_TOKENS=2000`, `BATCH_PARALLEL_TIMEOUT_SEC=30`
- `BATCH_PARALLEL_ENABLED=true`

### Архитектура
- Параллельные сабвызовы через `llm_generate_tiered` (cheap-модель)
- Склейка ответов с нумерацией, заглушки для упавших пунктов
- `pending_items` в результате — для batch_continuation

---

## [KG] — Knowledge Graph модуль

### Добавлено
- `core/knowledge_graph.py` — модуль графа знаний: плоский JSONL + Qdrant-векторный слой
- `tests/test_knowledge_graph.py` — 32 теста (entity_id, serialization, flat persistence, search, CRUD)
- Регистрация: `KnowledgeGraph.` префикс в `_brain_extension_tool_prefixes()`

### Инструменты для LLM (TOOL_CALL)
- `KnowledgeGraph.entity_save` — сохранить сущность (тип, имя, свойства JSON)
- `KnowledgeGraph.entity_relate` — добавить связь между сущностями
- `KnowledgeGraph.entity_search` — семантический / полнотекстовый поиск
- `KnowledgeGraph.entity_delete` — удалить сущность

### Архитектура
- При `QDRANT_URL`: векторный поиск по эмбеддингам
- Без Qdrant: flat-режим (JSONL с ранжированным полнотекстовым поиском)
- Flat-зеркало всегда включено для надёжности
- Fallback: Qdrant → flat при ошибке
