# Installation

## Requirements

| Component | Version |
|-----------|---------|
| Python | 3.11+ |
| OS | Linux (recommended), macOS, Git Bash on Windows |
| CPU | 1 core (2 recommended for dev) |
| RAM | **1 GB + swap** (tight, +VPN), **4 GB recommended** |
| Disk | 5 GB min, **10+ GB** on VPS with logs |
| GPU | **Not required** — LLM via OpenRouter |
| Network | HTTPS to OpenRouter; optional LAN for SearXNG/Mem0 |

**Proven on real hardware:** 1 GB VPS + VPN (legacy), 4 GB VPS (prod), 3.5 GB LAN lab — see [System requirements](../SYSTEM_REQUIREMENTS.md).

## Automated bootstrap

```bash
cd /opt/gemma_agent
bash scripts/agent_bootstrap.sh
```

Creates:

- `venv/` + `pip install -r requirements.txt`
- `.env` from `.env.example` if missing
- `scripts/gemma_panel.local.conf` with Mem0 stub
- `data/runtime/` directories

## Control panel

```bash
bash scripts/gemma_panel.sh status
bash scripts/gemma_panel.sh start-all    # Mem0 stub + bot
bash scripts/gemma_panel.sh stop-all
bash scripts/gemma_panel.sh log
```

See [Panel](../user-guide/panel.md).

## External services

| Service | Required | Install |
|---------|:--------:|---------|
| OpenRouter | yes | Account + API key in `.env` |
| SearXNG | strongly recommended | `sudo bash scripts/searxng_install_native.sh` |
| Mem0 | recommended | Stub (default) or `scripts/apply_mem0_local_server.sh` |
| Piper TTS | optional | Model under `models/piper/` |

Details:

- [Web search](../features/web-search.md)
- [Memory](../features/memory.md)
- [Voice](../features/voice.md)

## systemd (optional)

```ini
# /etc/systemd/system/gemma_bot.service
[Service]
WorkingDirectory=/opt/gemma_agent
ExecStart=/opt/gemma_agent/venv/bin/python3 main.py
Restart=on-failure
```

Set `GEMMA_BOT_SERVICE=gemma_bot.service` in panel conf; `gemma_panel.sh start` uses systemctl.

## Verify

```bash
python scripts/gemma_status.py
python scripts/gemma_status.py --online
bash scripts/gemma_panel.sh mem0-health
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080
```

## Host permissions

```bash
GEMMA_FIX_DATA_OWNER=1 bash scripts/gemma_host_setup.sh /opt/gemma_agent
```
