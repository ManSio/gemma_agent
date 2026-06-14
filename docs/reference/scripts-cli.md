# Scripts CLI reference

| Script | Purpose |
|--------|---------|
| `scripts/print_repo_stats.py` | Verifiable test/CI counts |
| `scripts/pip_audit.sh` | Dependency CVE scan (`pip-audit`; `aiohttp<3.14` for aiogram — see `requirements.txt`) |
| `scripts/generate_encryption_key.py` | Generate `ENCRYPTION_KEY` for memory at-rest encryption |
| `scripts/agent_bootstrap.sh` | First-time install |
| `scripts/gemma_panel.sh` | Bot + Mem0 control |
| `scripts/gemma_status.py` | Read-only health (`--online`, `--smoke`) |
| `scripts/agent_security_audit.py` | Honest security check |
| `scripts/release_guard.py` | Pre-release pytest gates |
| `scripts/check_public_privacy.py` | Secret leak scan in git files |
| `scripts/searxng_install_native.sh` | Install SearXNG (sudo) |
| `scripts/apply_mem0_local_server.sh` | Deploy mem0_server.py |
| `scripts/apply_power_agent_env.py` | Enable power_agent profile (Goal Runner + verify) |
| `scripts/apply_personal_prod_env.py` | Stable family mode (disable noisy autonomy) |
| `scripts/backup.sh` | Backup `data/` and config |
| `scripts/generate_llms_txt.py` | Regenerate docs/llms.txt |
| `scripts/turns_search.py` | Search turns.jsonl |
| `scripts/snapshot_cache_latency.py` | Cache + latency snapshot (llm_usage, turns, stage_ms) |
| `scripts/metrics_period_report.py` | Daily agent vs LLM metrics + history JSONL |
| `scripts/daily_server_digest.py` | DAILY_OPS archive digests |
| `scripts/server_full_audit.py` | Weekly / dated server audit |
| `scripts/turn_contract_health.py` | TurnContract gates + fingerprint stall + `--regression` |
| `scripts/replay_turn_thread.py` | Structural replay turns/ops_trace |
| `scripts/prod_persisted_impact_audit.py` | Forensic: persisted state vs bad turns |
| `scripts/export_turn_regression_cases.py` | Export prod turns → regression fixture |

Panel subcommands: [Panel](../user-guide/panel.md)
