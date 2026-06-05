# Memory (Mem0)

## Options

| Mode | When | `.env` |
|------|------|--------|
| **Stub** (default bootstrap) | Dev, small circle | `MEM0_API_URL=http://127.0.0.1:8001`, panel `GEMMA_MEM0_USE_STUB=true` |
| **Local server** | Production LAN | `/opt/mem0_local` + `apply_mem0_local_server.sh` |
| **Cloud** | Managed Mem0 | `MEM0_API_URL=https://api.mem0.ai` + `MEM0_API_KEY` |

## Stub limitations

- JSON file on disk, substring search
- No embeddings, no multi-tenant isolation
- Fine for testing — **not** honest «enterprise memory»

## Start stub

```bash
bash scripts/gemma_panel.sh mem0-start
bash scripts/gemma_panel.sh mem0-health
```

## Brain integration

Module `memory` + Mem0 client in `core/mem0_memory/` — API prefix `v3` by default.

Slash: `/mem_list`, `/mem_search` (see `/help`).
