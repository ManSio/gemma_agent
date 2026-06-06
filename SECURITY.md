# Security Policy

Gemma Agent is a **single-tenant Telegram bot** for a small trusted circle — not a multi-tenant SaaS. This policy describes what we protect, what we do not, and how to report issues.

**Details:** [docs/security/security-model.md](docs/security/security-model.md)

---

## Reporting a vulnerability

After the repository is public:

1. Prefer **[GitHub Security Advisories](https://github.com/ManSio/gemma_agent/security/advisories/new)** (private report).
2. Do **not** open public issues with exploit steps, tokens, or user data dumps.

Include:

- Description and severity
- Affected file paths and versions
- Reproduction on current `master`
- Which trust boundary (below) is crossed

We do **not** operate a paid bug bounty program.

---

## Trust model

### What we implement

| Control | Purpose |
|---------|---------|
| `USER_ACCESS_APPROVAL_REQUIRED` | New users need admin approval |
| `ADMIN_USER_IDS` | Restricts `/admin_*`, `/diag` |
| Anti-flood / rate limits | Abuse throttling |
| `SecurityManager` | Suspicious links, file intake warnings |
| `security_layer` module | Optional encryption for tool payloads |
| `check_public_privacy.py` | Blocks secrets in git-tracked files |

### What we do **not** guarantee

1. **LLM boundary** — User text and web content go to OpenRouter. Prompt injection can affect replies.
2. **Mem0 stub** — Plain JSON on disk; not isolated multi-tenant memory.
3. **No E2E encryption** — Beyond Telegram transport, chat content is not E2E encrypted.
4. **SearXNG** — Queries are visible to your instance and search engines.
5. **Voice cloud** — STT/TTS fallback may send audio to third-party APIs.
6. **Misconfiguration** — `USER_ACCESS_APPROVAL_REQUIRED=false` opens the bot to anyone with the link.

Reports that only demonstrate the above **limitations** are out of scope for private security advisory — but welcome as documentation issues or hardening PRs.

---

## In-scope examples

- Admin command executable by non-admin
- Secret committed to git or logged in plaintext
- Path traversal in file intake
- Authentication bypass on ops HTTP API (if exposed)
- Remote code execution without admin intent

## Out-of-scope examples

- “LLM said something wrong” (quality, not CVE)
- Mem0 stub readable on disk by same OS user (documented)
- SearXNG query metadata leakage to engines (operator risk)

---

## Operator checklist

- [ ] `.env` chmod 600, never in git
- [ ] Narrow `ADMIN_USER_IDS`
- [ ] `USER_ACCESS_APPROVAL_REQUIRED=true` for untrusted audiences
- [ ] Rotate tokens if leaked
- [ ] Run `python scripts/agent_security_audit.py` before release

---

## Supported versions

| Version | Supported |
|---------|-----------|
| Latest release on `master` | yes |
| Older tags | best effort |
| Private forks | operator responsibility |

Replace `ManSio` when publishing — [config/repo_links.json](config/repo_links.json).
