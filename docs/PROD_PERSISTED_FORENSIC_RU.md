# Prod forensic: персистентное состояние vs качество ходов

> **Источник:** VPS `212.113.101.151`, `/srv/gemma_bot`, 2026-06-14  
> **Скрипт:** `scripts/prod_persisted_impact_audit.py`  
> **Связь с TurnContract:** [THREAD_STABILITY_INDEX_RU.md](THREAD_STABILITY_INDEX_RU.md)

---

## Метод

- Окно: **14 дней**, **337** реальных ходов (`turns.jsonl`, без scenario/pre_send)
- Корреляция: `behavior_store` JSON, ephemeral lessons, `routing_prefs.pending_correction`
- Плохие ходы: clarify/fallback/issues + эвристика topic mismatch

```bash
venv/bin/python3 scripts/prod_persisted_impact_audit.py --days 14
venv/bin/python3 scripts/prod_persisted_impact_audit.py --days 14 --json-out /tmp/persisted_impact.json
```

---

## Сводка цифр

| Метрика | Значение |
|---------|----------|
| Ephemeral lessons активных | **88** (84 без `anchor_user_q`) |
| Источники | `experience_rules` **42**, `telegram_button` 31, `dialogue_feedback` 13 |
| Ходы с ephemeral в prompt | **58 (17%)** |
| Плохие ходы | **48 (~14%)** |
| Topic mismatch (эвристика) | **16 (9.4%)** — погода 5, статья 10 |
| Активных behavior-сессий | 1 (`591226766`) |
| Bad rate до/после 13 июня | 7.9% (17/215) → 10.7% (13/122) — **n мало** |

**Версия на момент аудита:** `3.5.22` (git snapshot ~`7a485ae`).

---

## Находки по приоритету

### P0 — Старый код (не JSON)

Кластер **13 июня ~11:18–11:27 UTC**: на «какой сегодня день», «как меня зовут» — ответ с футером/шаблоном погоды.

- **Класс:** weather short-circuit + anti-echo отсутствует  
- **Fix в dev:** v3.5.23+ (`anti_echo_guard`, TurnContract)  
- **Действие:** deploy, не wipe

### P1 — `pending_correction` (живое состояние)

В `591226766__dm.json`:

```json
{
  "instruction": "На исходный вопрос «А погода в Минске?» ... не уходи в meta...",
  "user_excerpt": "Повтори",
  "turns_left": 4,
  "source": "telegram_button"
}
```

- **Эффект:** 4 хода в prompt подмешивается обязательная правка про погоду  
- **Сброс:** `/new` в личке или ручное удаление `routing_prefs.pending_correction`

### P2 — `experience_rules` (архитектурный задел)

42 урока с триггерами `general`, `news`, `explain` как **contains в тексте** (см. `core/experience_rules.py`).

- **Сейчас на prod:** сработало **3 хода** за 14 дней — не главный виновник  
- **Риск:** рост уроков + англ. слова в сообщении → ложные срабатывания  
- **`deactivate_legacy_ephemeral_lessons.py`:** experience_rules **не трогает**

### OK — не трогать

| Слой | Вердикт |
|------|---------|
| `user_facts` (имя, город) | Оставить |
| `recent_messages` (~10) | Норма |
| `dialogue_summary` | Пустой |
| Слоты `899999*` (тест) | 0 ходов, не влияют |

---

## Рекомендуемый план (безопасный)

```bash
cd /srv/gemma_bot

# 1. Аудит
venv/bin/python3 scripts/prod_persisted_impact_audit.py --days 14 --json-out /tmp/persisted_impact.json

# 2. Деплой (3.5.27+)
bash scripts/gemma_panel.sh update

# 3. TurnContract verify
bash scripts/gemma_panel.sh turn-health

# 4. Точечная гигиена (НЕ полный wipe)
# Telegram: /new  ИЛИ  сброс pending_correction в behavior JSON

# 5. Legacy 👎 (на prod = 0)
venv/bin/python3 scripts/deactivate_legacy_ephemeral_lessons.py
```

---

## Открытые задачи (код, не prod-гигиена)

| # | Задача | Почему |
|---|--------|--------|
| 1 | Не писать `intent` как contains-триггер в `experience_rules` | Ложные матчи в тексте |
| 2 | Скрипт деактивации `source=experience_rules` | Разовая чистка prod |
| 3 | TTL / cap на `experience_rules` уроки | Не копить 88+ |
| 4 | Авто-сброс `pending_correction` после N ходов без feedback | Меньше «залипания» |

---

## Разделение ответственности

```
TurnContract (трек A)     → свежий STM, send=store, anti_echo, telemetry
Persisted forensic (B)    → что в JSON реально давит на prompt
Deploy                    → без него трек A не работает на prod
Wipe                      → только точечный, после deploy
```
