# Troubleshooting

| Symptom | Fix |
|---------|-----|
| Bot silent | `gemma_panel.sh status`; another host with same `TELEGRAM_TOKEN` |
| `Bot exited` in panel log | `tail panel_nohup_bot.log`; missing `TELEGRAM_TOKEN` |
| No web search | `curl $SEARXNG_INSTANCE_URL`; `SEARXNG_ENABLED=true` |
| Mem0 errors | `gemma_panel.sh mem0-health`; start stub or fix `MEM0_API_URL` |
| Wrong timezone reminders | `/me` city or `USER_TIMEZONE` |
| Duplicate greetings | Known cosmetic (greeting module + orchestrator) |
| Admin footer «weather» on wrong topic | Stale `weather_await_city` slot in behavior store — `/forget` or clear slot |
| `data/` permission denied | `GEMMA_FIX_DATA_OWNER=1 bash scripts/gemma_host_setup.sh` |
| Import / module errors | `bash scripts/agent_bootstrap.sh` |

## Diagnostics

```bash
python scripts/gemma_status.py --online
python scripts/agent_security_audit.py --quick
bash scripts/gemma_panel.sh preflight
```

## Get help

Open issue: URL from [REPO_LINKS.md](../REPO_LINKS.md) (after repo publish).
