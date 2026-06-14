# Стабильность нити — индекс расследований (2026-06-13/14)

> **Два параллельных трека** сходятся в один вывод: prod ломается **кодом + точечным state**, не «памятью целиком».

| Трек | Фокус | Документ | Код |
|------|--------|----------|-----|
| **A. TurnContract** | Контекст, доставка, телеметрия, lane | [TURN_CONTRACT_PLAN_RU.md](TURN_CONTRACT_PLAN_RU.md) | `core/turn_*.py` |
| **B. Persisted forensic** | behavior, ephemeral, pending_correction | [PROD_PERSISTED_FORENSIC_RU.md](PROD_PERSISTED_FORENSIC_RU.md) | `scripts/prod_persisted_impact_audit.py` |

**Runbook (deploy + gates + гигиена):** [TURN_CONTRACT_RUNBOOK_RU.md](TURN_CONTRACT_RUNBOOK_RU.md)

---

## Состояние на 2026-06-14

| Где | Версия | Статус |
|-----|--------|--------|
| **Репо (локально)** | **3.5.27** | TurnContract Phase 0–3 + forensic scripts; **не закоммичено** (проверь `git status`) |
| **VPS prod** | **3.5.22** | Jun 13 weather cluster — **старый код**; TurnContract **не выкачен** |

**Тесты (локально):** `pytest tests/` → **2859 passed**, 4 skipped (2026-06-14).

---

## Единый вердикт (не противоречит forensic)

| Вопрос | Ответ |
|--------|--------|
| Ломается ли из‑за всей памяти? | **Нет** — STM 10 реплик, facts ок, тестовые слоты не в hot path |
| Что давит **сейчас** на prod? | **v3.5.22** + **`pending_correction`** (живая латка на 4 хода) |
| Что давит **потенциально**? | **88 ephemeral** (42× `experience_rules` с contains=intent) |
| Поможет ли только wipe? | **Нет** без deploy — weather/нить вернутся из кода |
| Правильный порядок? | **Deploy 3.5.27** → точечная гигиена → 48h gates |

---

## Быстрые команды (VPS)

```bash
cd /srv/gemma_bot

# Forensic: персистентное состояние vs плохие ходы
venv/bin/python3 scripts/prod_persisted_impact_audit.py --days 14 --json-out /tmp/persisted_impact.json

# TurnContract: regression + gates (после deploy)
./scripts/gemma_panel.sh turn-health

# Деплой
./scripts/gemma_panel.sh update
```

---

## Хронология (кратко)

| Дата | Событие |
|------|---------|
| 2026-06-13 | Prod cluster: погода на identity/day; ops_trace 170 ходов |
| 2026-06-13 | TurnContract v3.5.23–27 в dev (anti_echo, defer store, lane, regression) |
| 2026-06-14 | Forensic audit: `pending_correction`, 88 ephemeral, bad rate 7.9%→10.7% (n мал) |

---

## Следующие шаги (приоритет)

1. **Commit + push** TurnContract + forensic scripts  
2. **`gemma_panel.sh update`** на VPS  
3. **`/new`** или сброс `pending_correction` для затронутого пользователя (на prod — через panel/behavior, ID не в git)  
4. **48h** `turn_contract_health` → Gate 0  
5. **Код (отдельный PR):** переписать `experience_rules` — не contains(intent) в тексте  

См. также: [DEV_DIARY_RU.md](DEV_DIARY_RU.md) (последние записи v3.5.23–27).
