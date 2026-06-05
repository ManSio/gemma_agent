# Web search (SearXNG)

UniversalSearch uses your own SearXNG instance — queries stay on your infrastructure (engines still see queries).

## `.env`

```env
SEARXNG_ENABLED=true
SEARXNG_INSTANCE_URL=http://127.0.0.1:8080
SEARXNG_MAX_RESULTS=8
```

## Install (same host)

```bash
sudo bash scripts/searxng_install_native.sh
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080
```

Template: `infra/searxng/settings.yml` → `/etc/searxng/settings.yml`

## Remote / LAN

```env
SEARXNG_INSTANCE_URL=http://10.0.0.10:8080
```

Bot process must reach this URL (firewall, bind `0.0.0.0` vs `127.0.0.1`).

## Verify in Telegram

«latest news about …» → article-style reply with sources line.

Without SearXNG: search degrades to LLM-only summaries (less grounded).
