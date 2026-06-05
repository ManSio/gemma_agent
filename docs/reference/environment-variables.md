# Environment variables (essential)

Full list: `.env.example` (6000+ lines) · fragments: `config/env_catalog/`

## Must set

| Variable | Purpose |
|----------|---------|
| `TELEGRAM_TOKEN` | Bot token |
| `OPENROUTER_API_KEY` | LLM API |
| `ADMIN_USER_IDS` | Admin Telegram ids |
| `OWNER_TELEGRAM_ID` | Owner id |

## Search & memory

| Variable | Default | Purpose |
|----------|---------|---------|
| `SEARXNG_ENABLED` | true | Web search |
| `SEARXNG_INSTANCE_URL` | http://127.0.0.1:8080 | SearXNG base URL |
| `MEM0_LOCAL` | true | Local Mem0 mode |
| `MEM0_API_URL` | http://127.0.0.1:8001 | Mem0 HTTP |
| `MEM0_API_PREFIX` | v3 | API version path |

## Access

| Variable | Recommended |
|----------|-------------|
| `USER_ACCESS_APPROVAL_REQUIRED` | true |
| `TELEGRAM_PIPELINE_PRIVATE_PARALLEL` | 1 |

## Optional voice

`VOICE_ENABLED`, `VOICE_TTS_*`, `VOICE_STT_*` — see [Voice](../features/voice.md)

## Sync helpers

```bash
python scripts/sync_env_from_example.py   # if present in tree
python scripts/apply_env_catalog.py       # apply catalog flags
```
