# Configuration

Primary file: **`.env`** in repo root (never commit).

Full catalog: `.env.example` + `config/env_catalog/*.env.fragment`

## Required secrets

| Variable | Source |
|----------|--------|
| `TELEGRAM_TOKEN` | @BotFather |
| `OPENROUTER_API_KEY` | openrouter.ai |
| `ADMIN_USER_IDS` | Your Telegram numeric id(s), comma-separated |
| `OWNER_TELEGRAM_ID` | Same as primary admin |

## Access control

```env
USER_ACCESS_APPROVAL_REQUIRED=true
USER_ACCESS_GUEST_REPLY_QUOTA=10
```

Set `false` only if you intentionally want an open bot.

## LLM routing

```env
OPENROUTER_MODEL_FREE=google/gemini-2.0-flash-001:free
OPENROUTER_MODEL_DEV=anthropic/claude-sonnet-4
OPENROUTER_HTTP_TOTAL_TIMEOUT_SEC=120
```

## Search

```env
SEARXNG_ENABLED=true
SEARXNG_INSTANCE_URL=http://127.0.0.1:8080
SEARXNG_MAX_RESULTS=8
```

## Memory

```env
MEM0_LOCAL=true
MEM0_API_URL=http://127.0.0.1:8001
MEM0_API_PREFIX=v3
```

## Telegram behavior

```env
TELEGRAM_PIPELINE_PRIVATE_PARALLEL=1
TELEGRAM_REPLY_MODE_FOOTER=admin
WEBHOOK_URL=
```

Empty `WEBHOOK_URL` → polling (default for small deploys).

## Voice (optional)

```env
VOICE_ENABLED=true
VOICE_TTS_ENABLED=true
VOICE_TTS_BACKEND=piper
VOICE_TTS_MODEL_PATH=./models/piper/ru_RU-irina-medium.onnx
```

## Panel overrides

`scripts/gemma_panel.local.conf`:

```bash
BOT_DIR=/opt/gemma_agent
GEMMA_MEM0_USE_STUB=true
MEM0_PORT=8001
```

## Env profiles

| Profile | Command | When |
|---------|---------|------|
| **Default** (`.env.example`) | — | Chat-first, stable for 3–8 users |
| **power_agent** | `python scripts/apply_power_agent_env.py` | Multi-step goals + self-verify + quality loop |
| **personal_prod** | `python scripts/apply_personal_prod_env.py` | Disable noisy autonomy for family prod |

Fragment reference: `config/power_agent.env.fragment` — see [Agent loop](../AGENT_LOOP.md).

## Reference

[Environment variables](../reference/environment-variables.md)
