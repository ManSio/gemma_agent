# Gemma Agent (public)

Telegram AI agent: brain-centric pipeline, tools, optional modules.

## Quick start

1. Copy `.env.example` → `.env`, set `TELEGRAM_TOKEN`, `OPENROUTER_API_KEY`.
2. `python -m venv venv && venv/Scripts/pip install -r requirements.txt` (Windows) or `venv/bin/pip` (Linux).
3. External: [SearXNG](https://docs.searxng.org/) on `http://127.0.0.1:8080` (optional Mem0 stub).
4. `python main.py` or `bash scripts/gemma_panel.sh start`.

## Public vs private

This tree is exported from a private fork. Removed: region-specific law/edu (BY), owner spatial module, dormant plugins, ops docs, secrets.

## Docs

- `docs/BRAIN_TOOLS_RU.md` — brain tools
- `docs/PLUGIN_AUTHOR_HANDBOOK_RU.md` — plugins
- `docs/TESTING_QUALITY_RU.md` — tests
- `docs/PUBLISHING_PRIVATE_RU.md` — export notes

## License

MIT — see LICENSE.
