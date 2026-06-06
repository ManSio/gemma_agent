# Security model (honest)

What Gemma Agent actually protects — and what it does not.

## In scope (implemented)

| Layer | What it does |
|-------|----------------|
| `check_public_privacy.py` | Scans git-tracked files for IPs, tokens, real user IDs |
| `SecurityManager` | Flood limits, suspicious links, file intake warnings |
| `security_layer` module | Fernet/AES encryption for tool payloads (`ENCRYPTION_KEY`) |
| `encrypted_json_store` | Mem0 stub + `facts.json` encrypted at rest when `ENCRYPTION_KEY` set |
| `prompt_injection_guard` | Strips injection lines from user text before LLM (`PROMPT_INJECTION_GUARD_ENABLED`) |
| `untrusted_content_sanitize` | Filters web/paste content for indirect injection |
| `pipeline_early_guards` | Blocks obvious exfiltration/jailbreak without LLM call |
| `USER_ACCESS_APPROVAL_REQUIRED` | Gate new users until admin approves |
| `ADMIN_USER_IDS` | Restricts `/admin_*`, `/diag`, dangerous ops |
| Anti-flood / rate limits | Per-user message throttling |
| `.gitignore` | `.env`, `data/`, local privacy blocklists |

## Out of scope / limitations

1. **LLM trust boundary** — Mitigated (not eliminated): line-level injection filter, early guards for exfiltration, web content sanitize. Sophisticated injection can still affect replies. No cryptographic proof of answer correctness.

2. **Mem0 stub at rest** — With `ENCRYPTION_KEY` (Fernet), `data/mem0_stub_store.json` and `facts.json` are encrypted on disk (`chmod 600`). Without key: plain JSON (dev only). Use Mem0 server + auth for multi-user isolation.

3. **No app-level E2E** — Telegram transport is encrypted by Telegram. Bot↔OpenRouter is HTTPS. Chat content is not separately E2E-encrypted by this app; tool payloads can use `security_layer`.

4. **SearXNG leakage** — Queries expose topics to your SearXNG instance and upstream engines.

5. **Voice cloud path** — If `VOICE_STT_FALLBACK_BACKEND=openrouter` or OpenAI STT, audio may leave your host.

6. **Misconfiguration risk** — `USER_ACCESS_APPROVAL_REQUIRED=false` opens the bot to anyone with the link.

7. **Dependency CVEs** — Run `pip audit` / update `requirements.txt` periodically; `release_guard` does not replace dependency scanning.

## Audit commands

```bash
python scripts/generate_encryption_key.py   # add to .env as ENCRYPTION_KEY
python scripts/agent_security_audit.py
python scripts/check_public_privacy.py --ci
python scripts/release_guard.py
pytest tests/test_security_layer.py -q
```

Exit code 1 = fix before claiming "secure release".

## Production checklist

- [ ] `.env` chmod 600, not in git  
- [ ] `ADMIN_USER_IDS` and `OWNER_TELEGRAM_ID` set to real admins only  
- [ ] `USER_ACCESS_APPROVAL_REQUIRED=true` unless intentional public beta  
- [ ] Rotate tokens if ever pasted in chat, logs, or commit  
- [ ] SearXNG bound to LAN or localhost if possible  
- [ ] `ENCRYPTION_KEY` set (memory at rest) — `python scripts/generate_encryption_key.py`  
- [ ] Mem0 stub replaced with authenticated server for untrusted users  
- [ ] `TELEGRAM_REPLY_MODE_FOOTER=admin` or `off` for non-debug users  

## Reporting issues

Contact the repository owner privately. Do not post tokens or user dumps in public issues.
