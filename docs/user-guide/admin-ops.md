# Admin & operations

Requires `ADMIN_USER_IDS` in `.env`.

## In Telegram

| Command | Purpose |
|---------|---------|
| `/diag` | Short health HTML: boot, flags, log tail |
| `/admin_xray` | Deeper pipeline diagnostics |
| `/admin_diagnostic` | ZIP diagnostic bundle |

## On server (read-only)

```bash
python scripts/gemma_status.py
python scripts/gemma_status.py --online
python scripts/turns_search.py "weather" --days 3
PYTHONPATH=. python scripts/snapshot_cache_latency.py --root . --hours 24
```

Cache/latency runbook: [CACHE_LATENCY_METRICS.md](../CACHE_LATENCY_METRICS.md)

## Logs

| File | Content |
|------|---------|
| `data/users/logs/gemma_bot.log` | Structured app log |
| `data/runtime/turns.jsonl` | Turn metadata (not full chat text) |
| `panel_nohup_bot.log` | stdout from panel start |

Full chat text: `data/users/behavior/*.json` — see architecture doc.

## Autopilot digests (optional)

```env
GEMMA_AUTOPILOT_MODE=on
AUTOPILOT_DIGEST_HOURS_UTC=8,20
```

Scheduled summaries to admin DM.

## What is off on production by design

- MCE / goal runner auto
- Mesh / parallel agents
- 47 dormant modules (denylist)
