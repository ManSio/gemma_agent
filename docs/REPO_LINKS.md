# Repository links (fill before publish)

Edit `config/repo_links.json` and search-replace `REPLACE_ORG` in README files.

| Placeholder | Set to |
|-------------|--------|
| `REPLACE_ORG` | GitHub org or username |
| `github_url` | `https://github.com/<org>/gemma-agent` |
| `docs_site_url` | GitHub Pages or future docs host (optional) |
| `issues_url` | GitHub Issues URL |

After the repo exists:

```bash
# Example: update badges in README.md
# [![GitHub](https://img.shields.io/github/stars/ORG/gemma-agent)](https://github.com/ORG/gemma-agent)
```

## GitHub About (sidebar)

Copy from `config/repo_links.json`:

- **Description:** `about_description`
- **Website:** `docs_site_url` or link to `docs/index.md`
- **Topics:** `github_topics` array

Enables automatic links: Contributing → `CONTRIBUTING.md`, Security → `SECURITY.md`, License → `LICENSE`.

Full steps: [PUBLISH_CHECKLIST.md](PUBLISH_CHECKLIST.md)

Docs: [index.md](index.md) · [index.ru.md](index.ru.md)
