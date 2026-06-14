# Ops digest (2026-06-14)

> Forensic backfill с VPS `212.113.101.151`. Полный отчёт: [PROD_PERSISTED_FORENSIC_RU.md](../PROD_PERSISTED_FORENSIC_RU.md)

## VPS_PROD

- **Версия:** v3.5.22 (репо локально: v3.5.27, не на prod)
- **Окно:** 14d, **337** реальных ходов
- **Bad rate:** ~14% (48 ходов); до/после Jun13: 7.9% → 10.7% (n мал)
- **Ephemeral active:** 88 (experience_rules 42)
- **Ephemeral hits:** 58 ходов (17%)
- **pending_correction:** активен у `900000001` (ID в публичной копии заменён), turns_left=4
- **Jun13 weather cluster:** 5 ходов — **код**, fix в 3.5.23+

## Действия

1. `gemma_panel.sh update` → 3.5.27  
2. `turn-health` + `prod_persisted_impact_audit.py --days 14`  
3. `/new` или сброс pending_correction  
4. Не делать полный wipe без deploy

## Индекс

[THREAD_STABILITY_INDEX_RU.md](../THREAD_STABILITY_INDEX_RU.md)
