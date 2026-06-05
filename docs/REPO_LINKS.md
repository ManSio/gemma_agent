# Repository links (single source of truth)

Edit [`config/repo_links.json`](../config/repo_links.json), then:

```bash
python scripts/apply_repo_links.py
```

| Field | Example (this repo) |
|-------|---------------------|
| `github_org` | `ManSio` |
| `github_repo` | `gemma_agent` |
| `default_branch` | `master` |
| `github_url` | `https://github.com/ManSio/gemma_agent` |

The script rewrites stale URLs (`gemma-agent`, `ManSio`, `/blob/master/` on `master` branch) across docs, `.github/`, code defaults, and `.env.example`.

Badge example:

```markdown
[![GitHub](https://img.shields.io/github/stars/ManSio/gemma_agent)](https://github.com/ManSio/gemma_agent)
```
