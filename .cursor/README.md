# Cursor config — gemma_agent

**Canonical agent setup for this repo.** Rules load automatically; skills load by task; prompts are copy-paste starters.

## Layout

```
.cursor/
├── README.md                 ← you are here
├── rules/                    ← always-on (short)
│   ├── agent-workflow.mdc
│   ├── gemma-ground-truth.mdc
│   └── python-changes.mdc
├── skills/                   ← full workflows (agent reads when relevant)
│   ├── gemma-agent/
│   │   ├── SKILL.md          ← code, fixes, features, docs, general work
│   │   └── reference.md
│   └── gemma-deep-audit/
│       ├── SKILL.md          ← repo / security / architecture audits
│       └── reference.md
└── prompts/                  ← paste into new chat (user)
    ├── audit.md · audit.ru.md
    ├── implement.md · implement.ru.md
    └── security.md · security.ru.md
```

## For the agent (Auto)

1. **Rules** — `alwaysApply: true` in every session.
2. **Skill `gemma-agent`** — default discipline for any task in this repo.
3. **Skill `gemma-deep-audit`** — when user asks for audit, review, оценка, вердикт.
4. **Read** `reference.md` inside the skill before large changes or audits.

## For the maintainer (you)

| Task | Use |
|------|-----|
| Any work | New Agent chat in repo (rules + skill auto) |
| Force audit mode | Paste `.cursor/prompts/audit.ru.md` or say «аудит по gemma-deep-audit» |
| Bug / feature | Paste `.cursor/prompts/implement.ru.md` |
| Security | Paste `.cursor/prompts/security.ru.md` |
| External AI (no Cursor) | `CHATGPT_PASTE.md` or clone + `AGENTS.md` |

After changing this tree: **new Agent chat** or reload window.

**Public entry:** [AGENTS.md](../AGENTS.md) · [docs/HONEST_POSITIONING.md](../docs/HONEST_POSITIONING.md)
