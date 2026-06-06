# System requirements

What you need to run Gemma Agent — based on **real production hosts** (not theoretical minimums).

**Audience:** 3–8 trusted Telegram users. LLM runs on **OpenRouter** (cloud) — no local GPU required.

---

## Proven reference hosts (live snapshot 2026-06-06)

Three real machines — not theory.

| | **VPS_LEGACY** (min) | **VPS_PROD** (prod) | **HOST_LAN** (lab) |
|--|---------------------|---------------------|-------------------|
| Role | Old VPS + **VPN stack** | Family prod bot | Dev / test bot |
| Hostname | `vm4934347` | `imperial-salmon.aeza.network` | `mansio-k53u` |
| **CPU** | **1 vCPU** | **1 vCPU** (Ryzen 5 3600 host) | AMD C-50, **2 cores** |
| **RAM** | **~1 GB** (961 Mi) | **3.8 GB** | **3.5 GB** |
| **Swap** | **512 MB** (required) | 1 GB | 512 MB |
| **Disk** | 15 GB (52% used) | 9.8 GB (62% used) | 233 GB (7% used) |
| Extra on host | **VPN** (was: x-ui, nginx, ocserv) | SearXNG + Mem0 + bot | SearXNG + bot |
| Deploy style | **native venv** + SearXNG **Docker image** on disk | native systemd | native systemd |
| Bot RSS (when running) | ~170 MB | **~172 MB** | **~188 MB** |
| Status Jun 2026 | archive; bot stopped; `searxng` image present | bot + Mem0 running | bot running |

**Takeaway:**

- **1 GB RAM + swap + VPN on same host** — works (tight; our legacy VPS proved it).
- **4 GB RAM, 1 vCPU** — comfortable daily prod (current VPS_PROD).
- **2-core old laptop CPU, 3.5 GB** — fine for lab/dev.

---

## Minimum vs recommended

| Component | **Absolute min** | Comfortable prod | Notes |
|-----------|:----------------:|:----------------:|-------|
| **CPU** | 1 core | 1–2 cores | Bot is I/O-bound; CPU rarely limits |
| **RAM** | **1 GB + swap** | **4 GB** | See RAM budget below |
| **Disk** | 5 GB | **10–15 GB** | `data/`, logs, VPN certs, venv |
| **GPU** | — | — | **Not needed** — OpenRouter handles LLM |
| **Network** | HTTPS outbound | stable uplink | OpenRouter + Telegram API |
| **OS** | Linux x86_64 | Ubuntu 22.04/24.04 | macOS OK; Windows = dev only |
| **Python** | 3.11 | 3.12+ | See `requirements.txt` |

### RAM budget (why 1 GB can work)

| Process | Typical RSS |
|---------|------------|
| `main.py` (bot) | **~170–190 MB** |
| SearXNG | ~80–150 MB |
| Mem0 stub | ~50–80 MB |
| Mem0 full server | +100–200 MB (skip on 1 GB) |
| **VPN** (x-ui + xray + nginx) | **~100–250 MB** |
| OS + buffers | ~200–300 MB |

On **1 GB** host: enable **swap (512 MB+)**, use **Mem0 stub** (not full server), expect pressure under load — but it **does run** (legacy VPS + VPN).

On **4 GB** host: full stack (bot + SearXNG + Mem0 server) without worry.

### Optional (adds disk/RAM)

| Feature | Extra needs |
|---------|-------------|
| Piper TTS | ~50–100 MB model in `models/piper/` |
| Vosk STT | ~50–500 MB model path |
| Qdrant RAG (books) | +RAM, optional separate service |
| `power_agent` profile | more LLM calls → latency/cost, not CPU |

---

## Software stack (what runs on the host)

```
┌─────────────────────────────────────────┐
│  gemma_bot / gemma_agent (main.py)      │  ~170–200 MB RAM
│  Telegram polling, orchestrator, plugins  │
├─────────────────────────────────────────┤
│  SearXNG (systemd, port 8080)           │  web search
├─────────────────────────────────────────┤
│  Mem0 server OR stub (port 8001)        │  stub on 1 GB; server on 4 GB
├─────────────────────────────────────────┤
│  VPN (optional, same VPS)               │  x-ui / xray / nginx / ocserv
├─────────────────────────────────────────┤
│  OpenRouter API (external, HTTPS)       │  all LLM inference
└─────────────────────────────────────────┘
```

Install paths on servers: `/srv/gemma_bot`, Mem0 at `/srv/mem0_local` (optional on small RAM).

---

## Where to deploy

| Environment | Works? | Notes |
|-------------|:------:|-------|
| **1 GB VPS + swap + VPN** | ✅ tight | Proven legacy host (~961 Mi RAM, 512 Mi swap) |
| **4 GB VPS** (€2–5/mo, 1 vCPU) | ✅ comfortable | Current prod — bot + SearXNG + Mem0 |
| **Home LAN server** (old x86 laptop) | ✅ | Lab — AMD C-50, 3.5 GB RAM |
| **Docker Compose** | ✅ | **2+ GB** comfortable; on 1 GB — SearXNG container proven on legacy; bot usually native |
| **systemd + native** | ✅ | Recommended for VPS (our prod) |
| **Windows** | ⚠️ dev | `python main.py` manually, no systemd |
| **Raspberry Pi 4 (1–4 GB)** | ⚠️ untested | 1 GB = very tight; 4 GB likely OK |
| **Local GPU for LLM** | ❌ not used | Project uses OpenRouter, not on-device inference |

### Tips for 1 GB + VPN hosts

- Enable **swap** (`512 MB` minimum, `1 GB` better).
- Use **Mem0 stub** (`GEMMA_MEM0_USE_STUB=true`), not full `mem0_server`.
- **Bot via native venv** (`gemma_panel.sh`) — how legacy 1 GB host actually ran.
- SearXNG: native systemd **or** Docker container (legacy host had `searxng/searxng` image).
- Avoid `power_agent` profile — extra LLM calls, not RAM but latency.
- Monitor: `free -h`, `scripts/gemma_status.py`, `/admin_xray_json` (admin).

### Docker (honest notes)

| Layout | 1 GB | Evidence |
|--------|:----:|----------|
| VPN + **native bot** (venv) + SearXNG Docker | ✅ tight | Legacy VPS — verified Jun 2026 |
| VPN + native bot + SearXNG **native** | ✅ tight | Current prod moved off Docker (Jun 2026) |
| Bot + SearXNG + Mem0 **all in Docker** | ❌ | Too tight on 1 GB |
| Full `docker compose` on **4 GB** VPS | ✅ | Used during migration 04.06, then native |

`dockerd` adds **~80–150 MB**. On 4 GB prod we **removed** Docker for SearXNG to save RAM.

```bash
# Standard Docker (2+ GB RAM)
cp .env.example .env
docker compose build app
docker compose up -d app
```

Small-host details: [DEPLOY.md — Docker](DEPLOY.md#option-b--docker-compose).

---

## Performance expectations (real prod metrics)

Not hardware limits — **OpenRouter latency** dominates.

| Metric | Typical value |
|--------|---------------|
| LLM call median | **5–9 s** |
| Full turn p50 (VPS) | **~14 s** |
| Full turn p90 (VPS) | **~51 s** |
| Bot process RSS | **~170–190 MB** |
| Concurrent users | **3–8** (design target) |

Bottleneck is LLM API + long tool chains, not VPS CPU (confirmed on €2-tier host).

---

## Tests & CI (developer machine)

| | |
|--|--|
| Test files | **410** (`tests/test_*.py`) |
| Test cases | **2580+** (`python scripts/print_repo_stats.py`) |
| CI Python | 3.12 (GitHub Actions) |
| Local run | `pip install -r requirements-dev.txt && pytest tests/ -q` |
| Fast gate | `python scripts/release_guard.py --smoke` (~1 min) |
| Full gate | `python scripts/release_guard.py --full` (long) |

Dev machine: any OS with Python 3.11+ and ~2 GB free RAM for pytest. No GPU.

---

## Collect fresh host stats (maintainer)

```bash
# On server
python scripts/gemma_status.py
python scripts/gemma_status.py --online

# Manual snapshot
hostname && nproc && free -h && df -h / && uptime
ps -o pid,rss,etime,cmd -p $(pgrep -f main.py | head -1)
```

Remote (from PC with SSH):

```bash
ssh user@your-host 'nproc && free -h && df -h / && pgrep -af main.py | head -1'
```

---

## Related docs

- [Installation](getting-started/installation.md) — bootstrap steps
- [Deployment](DEPLOY.md) — VPS, Docker, backups
- [Features overview](features/overview.md) — what needs which service
- [Agent loop](AGENT_LOOP.md) — `power_agent` vs default profile
