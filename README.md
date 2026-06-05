<p align="center">
  <img src="assets/gemma-agent-logo.png" alt="Gemma Agent" width="120"/>
</p>

# Gemma Agent

**Telegram assistant for a small trusted circle** — dialogue memory, routing, tools when needed.

<p align="center">
  <a href="docs/index.md"><img src="https://img.shields.io/badge/Docs-readme-0ea5e9?style=for-the-badge" alt="Docs"></a>
  <a href="https://github.com/ManSio/gemma-agent"><img src="https://img.shields.io/badge/GitHub-ManSio-181717?style=for-the-badge&logo=github" alt="GitHub"></a>
  <a href="README.ru.md"><img src="https://img.shields.io/badge/Lang-Русский-red?style=for-the-badge" alt="RU"></a>
</p>

Replace `ManSio` when the repo is published — see [docs/PUBLISH_CHECKLIST.md](docs/PUBLISH_CHECKLIST.md).

---

## Resources

| | |
|---|---|
| [Documentation](docs/index.md) | Full guide — setup, features, architecture |
| [Contributing](CONTRIBUTING.md) | Dev setup, PR process, tests (GitHub **Contributing** tab) |
| [Security policy](SECURITY.md) | Report vulnerabilities · honest trust model |
| [MIT License](LICENSE) | |
| [Code of Conduct](CODE_OF_CONDUCT.md) | |

---

## Quick install

```bash
git clone https://github.com/ManSio/gemma-agent.git /opt/gemma_agent
cd /opt/gemma_agent
bash scripts/agent_bootstrap.sh
# edit .env — TELEGRAM_TOKEN, OPENROUTER_API_KEY, ADMIN_USER_IDS
bash scripts/gemma_panel.sh start-all
python scripts/gemma_status.py --online
```

**Full guide:** [docs/getting-started/quickstart.md](docs/getting-started/quickstart.md)

---

## What it does

| Capability | Status |
|------------|--------|
| Chat, routing, skills | yes |
| Weather, search, reminders | yes (SearXNG + OpenRouter) |
| Long-term memory | yes (Mem0 stub or server) |
| Image / vision | yes (opt-in) |
| Voice STT/TTS | optional (Piper/Vosk) |
| MCE / mesh / spatial | no (public build) |

---

## Documentation

| Section | Link |
|---------|------|
| Hub | [docs/index.md](docs/index.md) |
| Installation | [docs/getting-started/installation.md](docs/getting-started/installation.md) |
| Configuration | [docs/getting-started/configuration.md](docs/getting-started/configuration.md) |
| Security (honest) | [docs/security/security-model.md](docs/security/security-model.md) |
| Architecture | [docs/developer-guide/architecture.md](docs/developer-guide/architecture.md) |
| Testing | [docs/developer-guide/testing.md](docs/developer-guide/testing.md) |

For LLMs: [docs/llms.txt](docs/llms.txt)

---

## Verify before release

```bash
python scripts/release_guard.py
python scripts/check_public_privacy.py --ci
python scripts/agent_security_audit.py
```

---

## License

See repository license file after publish.
