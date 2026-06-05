# Quickstart

From zero to a responding Telegram bot in about 15 minutes.

## Who this is for

- First install on Linux VPS or home LAN server
- Smoke test on Windows (manual `python main.py`)
- You have Telegram bot token and OpenRouter API key

## Fastest path

| Goal | Do this |
|------|---------|
| Full server setup | `bash scripts/agent_bootstrap.sh` → edit `.env` → `bash scripts/gemma_panel.sh start-all` |
| Verify | `python scripts/gemma_status.py --online` |
| Chat in Telegram | Send «hello» to your bot |

**Rule:** one clean reply in Telegram before tuning models, voice, or search.

## Steps

### 1. Clone and bootstrap

```bash
git clone https://github.com/ManSio/gemma_agent.git /opt/gemma_agent
cd /opt/gemma_agent
bash scripts/agent_bootstrap.sh
```

### 2. Minimum `.env`

```env
TELEGRAM_TOKEN=
OPENROUTER_API_KEY=
ADMIN_USER_IDS=
OWNER_TELEGRAM_ID=
USER_ACCESS_APPROVAL_REQUIRED=true
SEARXNG_ENABLED=true
SEARXNG_INSTANCE_URL=http://127.0.0.1:8080
MEM0_LOCAL=true
MEM0_API_URL=http://127.0.0.1:8001
```

### 3. Start

```bash
bash scripts/gemma_panel.sh start-all
```

Mem0 stub starts on port 8001 by default (`GEMMA_MEM0_USE_STUB=true` in `scripts/gemma_panel.local.conf`).

### 4. Telegram check

1. `/start`
2. `weather in London` (needs external API + OpenRouter)
3. `latest news` (needs SearXNG — see [Web search](../features/web-search.md))

### 5. Security sanity

```bash
python scripts/agent_security_audit.py --quick
```

## Windows (dev only)

```powershell
python -m venv venv
.\venv\Scripts\pip install -r requirements.txt
copy .env.example .env
.\venv\Scripts\python main.py
```

Stop any other host using the same `TELEGRAM_TOKEN`.

## Next

- [Installation](installation.md) — services, SearXNG, Mem0
- [Configuration](configuration.md) — all important env keys
- [Troubleshooting](../user-guide/troubleshooting.md)
