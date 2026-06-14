# Turn Contract — план стабилизации нити диалога

> **Версия плана:** 2026-06-14 · Код **3.5.27** (локально)  
> **Индекс + prod gap:** [THREAD_STABILITY_INDEX_RU.md](THREAD_STABILITY_INDEX_RU.md)  
> **Runbook:** [TURN_CONTRACT_RUNBOOK_RU.md](TURN_CONTRACT_RUNBOOK_RU.md)  
> **Forensic (параллельное расследование):** [PROD_PERSISTED_FORENSIC_RU.md](PROD_PERSISTED_FORENSIC_RU.md)  
> Контекст: расследование потери нити (prod Jun 13, ops_trace, 170 ходов / 7 дней)

---

## Зачем

Система **модульная и гибкая** (29 профилей, prompt_modules, short-circuits) — это сила.  
Проблема не в гибкости, а в **отсутствии единого контракта на ход** и **рассинхроне контекста/доставки**.

Три класса сбоев (подтверждены prod + код):

| Класс | Симптом | Пример |
|-------|---------|--------|
| **A. Контекст** | Brain/plan видит старый `recent_dialogue` | Jun 13: `recent_before` одинаков 9 мин |
| **B. Доставка** | В TG ушёл один текст, в store — другой | 8.2% `recent_assistant_text_mismatch` |
| **C. Наблюдаемость** | `turn_meaning` / `discourse` пусты на early paths | 89% ходов без referent в логе |

Динамический промпт (класс D, profile hop ~37%) — **вторичен**, пока не закрыты A–C.

---

## Принцип: fixed spine + modular execution

```
inbound → TurnContract (generation++) → fresh STM load → plan/brain modules → pre_send guards → send → reconcile store → telemetry
```

Модули меняют **как** отвечать; контракт фиксирует **что считается текущей нитью** и **какой ход актуален**.

---

## TurnContract (5+ полей)

| Поле | Назначение |
|------|------------|
| `generation` | Токен хода; новый inbound → ++; stale ответ не шлём и не пишем |
| `trace_id` | Связь turns.jsonl ↔ ops_trace |
| `referent` | thread / agent / user / world (из TurnMeaning) |
| `lane` | DIALOGUE / FACT / DEEP (обёртка над profile) |
| `short_circuit` | weather_direct, news_direct, pre_llm, … |
| `topic_anchor` | Одна строка «о чём нить» для prompt (Phase 1.5) |
| `recent_fingerprint` | Хэш последних N реплик STM — alert при залипании |

Логически frozen на ход; patch только через reconcile + audit.

---

## Дорожная карта

### Phase 0 — «Включить свет» ✅ v3.5.27

| # | Задача | Файлы | Gate |
|---|--------|-------|------|
| 0.1 | `turn_contract` + audit dict | `core/turn_contract.py` | pytest |
| 0.2 | Telemetry в `turns.jsonl` | `turn_observer.py`, orchestrator emit | grep 100% полей |
| 0.3 | `plan_turn_meaning` на direct_reply paths | `turn_plan_finalize.py`, orchestrator | ✅ все direct |
| 0.4 | Alert: один fingerprint >5 мин | `turn_fingerprint_alert.py`, `turn_contract_health.py` | runbook |

**Gate 0:** 48h prod — `referent` и `recent_fingerprint` в каждом ходе.

### Phase 1 — «Починить контекст» (1–2 недели) ✅ старт 3.5.23

| # | Задача | Файлы | Закрывает |
|---|--------|-------|-----------|
| 1.1 | `refresh_dialogue_stm_from_disk` перед `_assemble_brain_context` | `behavior_store`, `orchestrator` | Jun 13 stale |
| 1.2 | `anti_echo_guard` (template + topic) | `anti_echo_guard`, `scenario_engine` | weather на «как меня зовут» |
| 1.3 | `reconcile_sent_assistant_text` после `pre_send` | `behavior_store`, `input_layer` | 8.2% mismatch |
| 1.3b | **`persist_turn_after_delivery`** (v3.5.24) — store только после send | `turn_delivery_store` | ghost assistant |
| 1.4 | `turn_generation` bump + stale drop | `behavior_store`, `input_layer`, orchestrator | гонки |
| 1.5 | `topic_anchor` block в prompt | `prompt_modules` | Phase 1.5 |

**Gate 1:** 7 дней prod:
- `recent_assistant_text_mismatch` < 2%
- нет кластера «один wttr на 4 вопроса»
- topic mismatch heuristic < 5%

### Phase 2 — «Стабилизировать решения» ✅ v3.5.25

| # | Задача | Файлы | Статус |
|---|--------|-------|--------|
| 2.1 | Sticky lane при `discourse stay` | `turn_lane_spine.py`, `turn_decision_spine.py` | ✅ |
| 2.2 | Correction → full prompt + `must_blocks` | `turn_correction_contract.py`, `hot_path.py` | ✅ |
| 2.3 | Single SC registry | `short_circuit_registry.py` | ✅ |
| 2.4 | `turn_hash` plan vs brain drift | `turn_hash.py`, `pipeline.py`, `turn_observer` | ✅ |
| 2.5 | LLM replay suite | `scripts/replay_turn_thread.py` | ✅ structural |
| 1.5 | `topic_anchor` prompt module | `prompt_modules.py` | ✅ |

**Gate 2:** 7 дней prod — correlation(profile_switch, mismatch) < 0.2.

### Phase 3 — «Упростить поверхность» ✅ v3.5.26

| # | Задача | Файлы | Статус |
|---|--------|-------|--------|
| 3.1 | 3 lane наружу (UI/ops) | `turn_lane_ops.py`, footer, admin_turns, admin_self | ✅ |
| 3.2 | Additive-only prompt modules mid-thread | `turn_prompt_additive.py`, `prompt_modules.py` | ✅ |
| 3.3 | 20 prod threads regression | `turn_regression.py`, fixtures, replay `--regression` | ✅ |

**Gate 3:** regression 20/20 green; lane summary в `/admin_self`.

---

## Зоны проекта (touch map)

| Зона | Phase | Риск |
|------|-------|------|
| `core/orchestrator.py` | 0–1 | plan early exits, execute_plan emit |
| `core/input_layer.py` | 1 | send path, stream, footer после pre_send |
| `core/behavior_store.py` | 1 | STM, generation в routing_prefs |
| `core/scenario_engine.py` | 1 | apply_pre_send chain |
| `core/brain/pipeline.py` | 1.5 | topic_anchor block |
| `core/turn_observer.py` | 0 | turns.jsonl schema |
| `core/ops_trace.py` | 0 | pairing check + fingerprint |
| `core/turn_shortcut_gate.py` | 2 | sticky + SC registry |
| `core/turn_decision_spine.py` | 2 | meaning lock + lane |
| `core/product_behavior.py` | 1 | echo (не дублировать anti_echo) |
| `core/batch_continuation.py` | 2 | generation на continuation |
| `tests/` | все | contract, anti_echo, reconcile |

**Не трогаем в Phase 0–1:** profile_registry rewrite, monolithic prompt, task.cancel().

---

## Риски «узнаем в бою»

| Риск | Митигация |
|------|-----------|
| Fresh reload затрёт in-plan мутации (location, timezone) | `refresh_dialogue_stm_from_disk` — только `recent_messages` + `dialogue_summary` |
| reconcile дважды пишет assistant | guard: patch только если текст отличается |
| generation stale отрежет легитимный медленный ответ | drop только при **новом inbound** (gen вырос); метрика `turn_generation_stale_*` |
| anti_echo ложные срабатывания | allow «повтори»; env `ANTI_ECHO_GUARD_ENABLED` |
| direct_reply без brain — нет fingerprint | contract из `plan_turn_meaning` + persisted at plan time |
| stream path обходит pre_send | уже через finalize; reconcile после edit |
| group chat parallel | generation per chat_key (user_id + group_id) |
| footer после pre_send меняет текст | reconcile **после** `append_mode_footer` |
| Phase 2 sticky lane ломает legitimate pivot | только при `discourse stay`, не blind lock |

---

## Метрики (MONITOR + turns.jsonl)

| Метрика | Ожидание после Phase 1 |
|---------|------------------------|
| `turn_contract_built_total` | ≈ plan_calls |
| `turn_generation_stale_send_skip_total` | редко (double-tap) |
| `turn_generation_stale_store_skip_total` | редко |
| `anti_echo_guard_total` | >0 при Jun13-class |
| `dialogue_stm_refresh_total` | ≈ brain plans |
| `sent_assistant_reconcile_total` | >0 когда pre_send меняет текст |
| `recent_assistant_text_mismatch` (ops) | < 2% |

---

## Verify (каждый PR)

```bash
python -m py_compile core/turn_contract.py core/anti_echo_guard.py
python -m pytest tests/test_turn_contract.py tests/test_anti_echo_guard.py tests/test_product_behavior.py -q
python scripts/release_guard.py --smoke
```

После deploy: 48h grep `recent_fingerprint`, `turn_generation`, `anti_echo` в turns.jsonl.

---

## Честный прогноз

| После | Эффект |
|-------|--------|
| Phase 0+1 (3.5.23) | −50–60% «потеря нити»; Jun13-class закрыт |
| Phase 2 | −ещё 20–30%; предсказуемость профилей |
| Phase 3 | ops-поверхность; не обязателен |

**Не упрощать систему — упрощать решения на ход.**
