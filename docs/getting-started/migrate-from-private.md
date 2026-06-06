# Replace private install with public build (single VPS, low RAM)

In-place swap with full rollback — not two parallel installs.

## Preserved

- `.env` — tokens and your settings  
- `data/` — memory, behavior, logs  
- Rollback — full tarball + `*_private_*` directory  

## Before

```bash
sudo systemctl stop gemma_bot.service
bash scripts/gemma_panel.sh stop-all
```

## Migrate

```bash
systemctl stop gemma_bot.service
bash scripts/migrate_private_to_public.sh --bot-dir /opt/gemma_agent --dry-run
bash scripts/migrate_private_to_public.sh --bot-dir /opt/gemma_agent
bash scripts/gemma_panel.sh start-all
python scripts/gemma_status.py --online
```

Preflight: disk ~2× bot dir, bot stopped, `TELEGRAM_TOKEN` + `OPENROUTER_API_KEY` in `.env`.

## Rollback

```bash
systemctl stop gemma_bot.service
bash scripts/rollback_to_private.sh --rollback-dir /opt/gemma_agent_private_STAMP --bot-dir /opt/gemma_agent
systemctl start gemma_bot.service
```

See `/var/backups/gemma/ROLLBACK_*.txt` after migrate.

**RU:** [migrate-from-private.ru.md](migrate-from-private.ru.md)
