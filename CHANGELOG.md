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
