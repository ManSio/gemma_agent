# TurnContract — runbook (prod verify + гигиена)

> **Индекс расследований:** [THREAD_STABILITY_INDEX_RU.md](THREAD_STABILITY_INDEX_RU.md)  
> **Forensic persisted:** [PROD_PERSISTED_FORENSIC_RU.md](PROD_PERSISTED_FORENSIC_RU.md)

---

## 1. Deploy (обязательно перед gates)

Prod на **3.5.22** не содержит TurnContract. Без deploy gates бессмысленны.

```bash
cd /srv/gemma_bot
bash scripts/gemma_panel.sh update   # git pull + pip + restart
```

Проверить `VERSION` / `git log -1` → ожидается **3.5.27+**.

---

## 2. После deploy — TurnContract

```bash
./scripts/gemma_panel.sh turn-health

# Детально
python scripts/turn_contract_health.py --regression --limit 500
python scripts/replay_turn_thread.py --regression
```

---

## 3. Gates

| Gate | Когда | Критерий |
|------|-------|----------|
| **0** | 48h | `referent_pct`, `fingerprint_pct` ≥ 90% |
| **1** | 7d | mismatch / anti_echo; `issues_pct` < 15% (proxy) |
| **2** | 7d | `drift_pct` < 20% |
| **3** | CI | regression 20/20 + lane в `/admin_self` |

```bash
python scripts/turn_contract_health.py --limit 500 --json
```

Fingerprint stall (0.4):

```bash
python scripts/turn_contract_health.py --json   # поле fingerprint_stalls
```

Env: `TURN_FINGERPRINT_STALL_MINUTES` (default 5), `TURN_FINGERPRINT_ALERT_ENABLED`.

---

## 4. Persisted гигиена (после deploy, без full wipe)

```bash
# Forensic audit
python scripts/prod_persisted_impact_audit.py --days 14 --json-out /tmp/persisted_impact.json

# Legacy ephemeral 👎
python scripts/deactivate_legacy_ephemeral_lessons.py
```

**Точечно:**

- `/new` в Telegram — сброс STM + `pending_correction`
- Или вручную: `routing_prefs.pending_correction` в behavior JSON

**Не делать:** полный wipe `data/` — не заменяет deploy.

---

## 5. Prod regression export

```bash
python scripts/export_turn_regression_cases.py --limit 20 \
  --out tests/fixtures/turn_regression_prod.json
python -m pytest tests/test_turn_contract_phase3.py -q
```

---

## 6. Флаги `.env`

```
TURN_CONTRACT_ENABLED=true
TURN_DEFER_STORE_ENABLED=true
ANTI_ECHO_GUARD_ENABLED=true
TURN_STICKY_LANE_ENABLED=true
TURN_CORRECTION_OVERRIDE_ENABLED=true
TURN_HASH_DRIFT_ENABLED=true
TURN_PROMPT_ADDITIVE_ENABLED=true
TURN_FINGERPRINT_ALERT_ENABLED=true
TURN_FINGERPRINT_STALL_MINUTES=5
```

---

## 7. Локальная verify (перед commit)

```bash
python -m pytest tests/ -q
python scripts/release_guard.py --smoke
python scripts/turn_contract_health.py --regression
```

**Ожидание:** 2859+ passed; regression 20/20.
