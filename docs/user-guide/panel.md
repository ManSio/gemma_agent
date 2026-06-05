# Control panel

`scripts/gemma_panel.sh` — bot + Mem0 lifecycle (~340 lines, no LLM studio bloat).

## Commands

```bash
bash scripts/gemma_panel.sh status
bash scripts/gemma_panel.sh start-all      # Mem0 then bot
bash scripts/gemma_panel.sh stop-all
bash scripts/gemma_panel.sh restart
bash scripts/gemma_panel.sh mem0-start
bash scripts/gemma_panel.sh mem0-health
bash scripts/gemma_panel.sh log
bash scripts/gemma_panel.sh preflight
bash scripts/gemma_panel.sh setup          # agent_bootstrap.sh
bash scripts/gemma_panel.sh security       # agent_security_audit.py
bash scripts/gemma_panel.sh update         # git pull + pip + restart
```

Interactive: `bash scripts/gemma_panel.sh` (menu).

## Config

`scripts/gemma_panel.local.conf`:

```bash
BOT_DIR=/opt/gemma_agent
GEMMA_MEM0_USE_STUB=true
MEM0_PORT=8001
```

Env overrides: `GEMMA_BOT_DIR`, `GEMMA_MEM0_USE_STUB`, `GEMMA_PANEL_CONFIG`.

## systemd

If `gemma_bot.service` is active, `start`/`stop` use systemctl instead of PID file.
