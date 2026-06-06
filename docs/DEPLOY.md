# Deployment guide

Production-oriented setup for a **single VPS or home server** (3–8 trusted users).

---

## Option A — Native (recommended for VPS)

```bash
git clone https://github.com/ManSio/gemma_agent.git /opt/gemma_agent
cd /opt/gemma_agent
bash scripts/agent_bootstrap.sh
# Edit .env: TELEGRAM_TOKEN, OPENROUTER_API_KEY, ADMIN_USER_IDS
bash scripts/gemma_panel.sh start-all
python scripts/gemma_status.py --online
```

### systemd

```ini
# /etc/systemd/system/gemma_bot.service
[Unit]
Description=Gemma Agent Telegram bot
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/gemma_agent
ExecStart=/opt/gemma_agent/venv/bin/python3 main.py
Restart=on-failure
RestartSec=10
Environment=PYTHONPATH=/opt/gemma_agent

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now gemma_bot.service
```

Set `GEMMA_BOT_SERVICE=gemma_bot.service` in `scripts/gemma_panel.local.conf`.

---

## Option B — Docker Compose

```bash
cp .env.example .env   # fill secrets
docker compose build
docker compose up -d app
docker compose logs -f app
```

| Service | Port | Notes |
|---------|------|-------|
| `app` | 8000 (reserved) | Bot mode — Telegram polling, no HTTP health |
| `api` | 8001 | Optional REST (`--profile api`) |

Volumes: `./data`, `./models`, `./modules` mounted into container.

### Docker on small RAM (1 GB + VPN)

**Verified on legacy ~1 GB VPS (Jun 2026):** native bot (`venv` + `gemma_panel.sh`), SearXNG **Docker image** on host, VPN stack (later migrated off).

| Component | How it ran on 1 GB legacy |
|-----------|---------------------------|
| Bot | **native** `/srv/gemma_bot/venv` — not `docker compose app` |
| SearXNG | **Docker** (`searxng/searxng` image on disk) |
| Mem0 | `/srv/mem0_local` native |
| VPN | x-ui / nginx / ocserv on host |

**Recommended today:** same as prod — **native systemd** for bot + SearXNG on 2+ GB; Docker optional for dev.

```bash
# Docker Compose (comfortable: 2+ GB RAM)
cp .env.example .env
docker compose build app
docker compose up -d app
```

On **1 GB**: enable swap; prefer native `bash scripts/gemma_panel.sh start-all` over full compose stack.

**Do not** on 1 GB: `docker compose --profile api`, Portainer, bot + SearXNG + Mem0 all containerized.

See [System requirements](SYSTEM_REQUIREMENTS.md).

---

## SearXNG (search)

**Native (prod):**

```bash
sudo bash scripts/searxng_install_native.sh
# .env: SEARXNG_INSTANCE_URL=http://127.0.0.1:8080
```

**Docker (dev/LAN):**

```bash
cd infra/searxng
docker compose up -d
```

---

## Mem0 (long-term memory)

| Mode | Start |
|------|-------|
| Stub (default) | `bash scripts/gemma_panel.sh mem0-start` |
| Local server | `bash scripts/apply_mem0_local_server.sh` |

Health: `bash scripts/gemma_panel.sh mem0-health`

Details: [features/memory.md](features/memory.md)

---

## Backups

```bash
# Full data + runtime snapshot
bash scripts/backup.sh

# Dry-run
bash scripts/backup.sh --dry-run

# Custom destination
bash scripts/backup.sh --dest /var/backups/gemma
```

Backs up: `data/`, `config/*.json` (non-secret), recent logs. **Never** commits `.env`.

Restore: stop bot → extract tarball → `gemma_panel.sh start-all` → verify with `gemma_status.py`.

---

## Monitoring (lightweight)

| Tool | Command |
|------|---------|
| Status | `python scripts/gemma_status.py --online` |
| Panel | `bash scripts/gemma_panel.sh status` |
| Logs | `bash scripts/gemma_panel.sh log` |
| Errors | `tail -f data/runtime_errors.jsonl` |
| Safe mode | `cat data/runtime/safe_mode_state.json` |

Resilience details: [SELF_HEALING.md](SELF_HEALING.md)

---

## Security before go-live

- [ ] `.env` chmod 600, not in git
- [ ] `ADMIN_USER_IDS` set
- [ ] `USER_ACCESS_APPROVAL_REQUIRED=true`
- [ ] `python scripts/check_public_privacy.py --ci`
- [ ] `python scripts/agent_security_audit.py`

Checklist: [security/security-model.md](security/security-model.md)

---

## Verify release

```bash
python scripts/release_guard.py
python -m pytest tests/test_plugin_contract.py -q
```
