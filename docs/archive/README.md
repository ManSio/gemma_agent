# Ops archive

Суточные и недельные ops-дайджесты (счётчики без PII/excerpts).

| Шаблон | Генерация |
|--------|-----------|
| `DAILY_OPS_YYYY-MM-DD_RU.md` | `python scripts/daily_server_digest.py --date YYYY-MM-DD` |
| Backfill диапазона | `python scripts/daily_server_digest.py --backfill-from YYYY-MM-DD --backfill-to YYYY-MM-DD` |
| `WEEKLY_OPS_*` | `python scripts/server_full_audit.py --days 7 --md-out docs/archive/WEEKLY_OPS_...` |

На VPS: `cd /srv/gemma_bot && venv/bin/python3 scripts/daily_server_digest.py ...`

JSON-снимки: `data/benchmarks/daily_digest_YYYYMMDD.json`
