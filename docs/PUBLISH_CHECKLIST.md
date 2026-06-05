# GitHub publish checklist

Use when creating the public repository (Hermes-style presentation).

## 1. Create repo

- Name: `gemma-agent` (or your choice)
- Public
- No `.env` in initial commit

## 2. Edit `config/repo_links.json`

```json
{
  "github_org": "YourOrg",
  "github_repo": "gemma-agent",
  "github_url": "https://github.com/YourOrg/gemma-agent",
  "docs_site_url": "https://YourOrg.github.io/gemma-agent/",
  "issues_url": "https://github.com/YourOrg/gemma-agent/issues"
}
```

## 3. Replace placeholders

```bash
python scripts/apply_repo_links.py
```

Or search-replace `ManSio` in README, CONTRIBUTING, SECURITY, `.github/`.

## 4. Repository About (right sidebar)

| Field | Suggested |
|-------|-----------|
| Description | Telegram assistant for a small trusted circle — memory, routing, tools |
| Website | `docs/index.md` on GitHub or future Pages URL |
| Topics | `telegram-bot`, `ai-agent`, `openrouter`, `python`, `llm`, `chatbot`, `searxng`, `mem0` |

## 5. Enable GitHub features

- [ ] Issues
- [ ] Discussions (optional)
- [ ] Actions (workflow `release-guard` already in repo)
- [ ] Security → Private vulnerability reporting

## 6. Links GitHub shows automatically

| Tab | File |
|-----|------|
| Contributing | [CONTRIBUTING.md](../CONTRIBUTING.md) |
| Security policy | [SECURITY.md](../SECURITY.md) |
| License | [LICENSE](../LICENSE) |

## 7. First release (optional)

Tag `v3.4.0` with notes:

- Public build — 19 modules
- Docs: `docs/index.md`
- Setup: `bash scripts/agent_bootstrap.sh`

## 8. Verify

```bash
python scripts/release_guard.py
python scripts/check_public_privacy.py --ci
python scripts/agent_security_audit.py
```
