# Security model (honest)

What Gemma Agent actually protects — and what it does not.

## In scope (implemented)

| Layer | What it does |
|-------|----------------|
| `check_public_privacy.py` | Scans git-tracked files for IPs, tokens, real user IDs |
| `SecurityManager` | Flood limits, suspicious links, file intake warnings |
| `security_layer` module | Optional Fernet encryption for tool payloads (`ENCRYPTION_KEY`) |
| `USER_ACCESS_APPROVAL_REQUIRED` | Gate new users until admin approves |
| `ADMIN_USER_IDS` | Restricts `/admin_*`, `/diag`, dangerous ops |
| Anti-flood / rate limits | Per-user message throttling |
| `.gitignore` | `.env`, `data/`, local privacy blocklists |

## Out of scope / limitations

1. **LLM trust boundary** — User text and retrieved web content go to OpenRouter. Prompt injection can manipulate replies. No cryptographic proof of answer correctness.

2. **Mem0 stub** — Stores memories in plain JSON. Any process with disk access can read them. Search is substring-only.

3. **No E2E encryption** — Telegram ↔ bot ↔ LLM is not end-to-end encrypted beyond Telegram's own transport.

4. **SearXNG leakage** — Queries expose topics to your SearXNG instance and upstream engines.

5. **Voice cloud path** — If `VOICE_STT_FALLBACK_BACKEND=openrouter` or OpenAI STT, audio may leave your host.

6. **Misconfiguration risk** — `USER_ACCESS_APPROVAL_REQUIRED=false` opens the bot to anyone with the link.

7. **Dependency CVEs** — Run `pip audit` / update `requirements.txt` periodically; `release_guard` does not replace dependency scanning.

## Audit commands

```bash
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
- [ ] Mem0 stub replaced with authenticated server for untrusted users  
- [ ] `TELEGRAM_REPLY_MODE_FOOTER=admin` or `off` for non-debug users  

## Reporting issues

Contact the repository owner privately. Do not post tokens or user dumps in public issues.
