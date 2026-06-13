# Снимок кэша и задержек — 2026-06-13 (VPS prod, 24h)

Сгенерировано: `scripts/snapshot_cache_latency.py --hours 24`  
Сервер: `/srv/gemma_bot` · коммит на момент снимка: `8de96c7`  
**Только счётчики** — без user_id, excerpts, имён.

---

## Кэш

| Метрика | Значение |
|---------|----------|
| Brain LLM вызовов | 22 |
| LLM ok / fail (все теги) | 58 / 6 |
| KV hit (вызовы с cached_prompt_tokens>0) | **18.2%** |
| Доля cached prompt tokens | **42.6%** |
| brain_response_cache_hit (MONITOR) | 0 |
| brain_prompt_cache_hit (MONITOR) | 0 |
| openrouter cached read tokens | 0 |
| brain recent limit r8 / r12 | 17 / 5 |

---

## Задержки

### llm_usage (brain)

| | ms |
|---|-----|
| p50 | 4704 |
| p95 | 9743 |
| all LLM p95 | 12587 |

### turns (end-to-end)

| | ms |
|---|-----|
| turns | 78 |
| issues | 2 |
| p50 | 5339 |
| p95 | 20935 |

### stage_ms (39 ходов с разбивкой)

| Стадия | p50 | p95 |
|--------|-----|-----|
| total | 7937 | 21459 |
| exec_modules_done | 5336 | 20935 |
| plan_done | 93 | 14715 |
| exec_start | 216 | 376 |
| pre_plan | 67 | 204 |

---

## Выводы (кратко)

1. **Узкое место — brain/OpenRouter** (`exec_modules_done` ≈ 97% p95 total).
2. **KV кэш провайдера** даёт ~43% cached tokens при ~18% hit rate по вызовам.
3. **Локальные кэши ответов** за окно не использовались.
4. Planner/pre_plan — доли секунды (медиана); хвост plan — редкие выбросы.

Runbook: [../CACHE_LATENCY_METRICS_RU.md](../CACHE_LATENCY_METRICS_RU.md)
