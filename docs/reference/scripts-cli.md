# Scripts CLI reference

| Script | Purpose |
|--------|---------|
| `scripts/print_repo_stats.py` | Verifiable test/CI counts (410 files, 2580+ cases) |
| `scripts/pip_audit.sh` | Dependency CVE scan (`pip-audit`, aiohttp pin documented) |
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

Panel subcommands: [Panel](../user-guide/panel.md)
