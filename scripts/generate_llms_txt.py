#!/usr/bin/env python3
"""Generate docs/llms.txt — curated documentation index for LLM agents."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

PAGES: list[tuple[str, str]] = [
    ("CONTRIBUTING.md", "Contributing — GitHub tab"),
    ("SECURITY.md", "Security policy"),
    ("docs/PUBLISH_CHECKLIST.md", "Publish repo on GitHub"),
    ("docs/index.md", "Documentation hub (EN)"),
    ("docs/index.ru.md", "Documentation hub (RU)"),
    ("docs/getting-started/quickstart.md", "Quickstart EN"),
    ("docs/getting-started/quickstart.ru.md", "Quickstart RU"),
    ("docs/getting-started/installation.md", "Installation EN"),
    ("docs/getting-started/configuration.md", "Configuration EN"),
    ("docs/getting-started/public-build.md", "Public build scope"),
    ("docs/user-guide/telegram.md", "Telegram usage"),
    ("docs/user-guide/admin-ops.md", "Admin commands and ops"),
    ("docs/user-guide/panel.md", "gemma_panel.sh"),
    ("docs/user-guide/troubleshooting.md", "Troubleshooting"),
    ("docs/features/overview.md", "Features matrix"),
    ("docs/features/web-search.md", "SearXNG"),
    ("docs/features/memory.md", "Mem0"),
    ("docs/features/voice.md", "Voice STT/TTS"),
    ("docs/features/modules.md", "19 modules"),
    ("docs/security/security-model.md", "Security (honest)"),
    ("docs/developer-guide/architecture.md", "Architecture"),
    ("docs/developer-guide/testing.md", "Testing"),
    ("docs/reference/environment-variables.md", "Essential env vars"),
    ("docs/reference/scripts-cli.md", "Scripts reference"),
]


def main() -> int:
    lines = [
        "# Gemma Agent — documentation index for LLMs",
        f"# Generated from {ROOT.name}",
        "",
    ]
    for rel, desc in PAGES:
        path = ROOT / rel.replace("/", "\\") if False else ROOT / rel
        path = ROOT / Path(rel)
        if path.is_file():
            lines.append(f"- {rel} — {desc}")
        else:
            lines.append(f"- {rel} — {desc} (MISSING)")
    out = DOCS / "llms.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out} ({len(lines)} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
