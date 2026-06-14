# Панель управления

`scripts/gemma_panel.sh` — бот + Mem0.

## Команды

```bash
bash scripts/gemma_panel.sh status
bash scripts/gemma_panel.sh start-all
bash scripts/gemma_panel.sh stop-all
bash scripts/gemma_panel.sh log
bash scripts/gemma_panel.sh setup
bash scripts/gemma_panel.sh security
bash scripts/gemma_panel.sh turn-health   # TurnContract gates + regression 20/20
bash scripts/gemma_panel.sh update        # git pull + pip + restart
```

После deploy: [TURN_CONTRACT_RUNBOOK_RU.md](../TURN_CONTRACT_RUNBOOK_RU.md).  
Forensic persisted: `python scripts/prod_persisted_impact_audit.py --days 14`.

Меню: `bash scripts/gemma_panel.sh`

## Конфиг

`scripts/gemma_panel.local.conf` — `BOT_DIR`, `GEMMA_MEM0_USE_STUB=true`.

## systemd

При активном `gemma_bot.service` — start/stop через systemctl.
