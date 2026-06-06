# Project context (offline summary)

Repository: https://github.com/ManSio/gemma_agent  
**Full Q&A:** [docs/HONEST_POSITIONING.md](docs/HONEST_POSITIONING.md) · **Clone preferred** over paste-only review.

---

## What it is

**Telegram assistant** for **3–8 trusted users** (not SaaS). Python, aiogram, **[OpenRouter](https://openrouter.ai/)** (GPT/Claude/Gemini — your choice).

| Topic | Fact |
|-------|------|
| Name `gemma_agent` | Historical project name — **not** Google Gemma |
| Intelligence | OpenRouter models |
| This repo | Orchestration: routing, memory, tools, guards, ops |
| Default mode | **Assistant** (route → LLM → reply) |
| Agent loops | Opt-in (`GOAL_RUNNER_ENABLED`, power_agent profile) |

**Timeline:** production from **2026-05-02**; public GitHub export **2026-06-06**.

---

## Verify in git

```bash
python scripts/print_repo_stats.py
python -m pytest tests/ --collect-only -q
```

| Fact | Value |
|------|-------|
| Tests | 410 files, **2580+** cases |
| CI | `.github/workflows/ci.yml` |
| Plugins (public) | 19 |
| Hot path | `input_layer` → `orchestrator` → `brain/pipeline` |

Raw files: branch **`main`** or **`master`** (e.g. `.../main/SECURITY.md`).

---

## Limits (documented)

- Not multi-tenant; trusted circle only
- Prompt injection mitigated, not eliminated
- Mem0 stub = weak substring retrieval; server Mem0 recommended
- **pytest ≠ live Telegram** — see [PRODUCTION_EVIDENCE_REPORT.md](docs/PRODUCTION_EVIDENCE_REPORT.md) §10
- Prod detail metrics: operator aggregates in report; raw jsonl stays private

---

## Production snapshot (May–Jun 2026)

Details: [docs/PRODUCTION_EVIDENCE_REPORT.md](docs/PRODUCTION_EVIDENCE_REPORT.md)

| Metric | Before → After |
|--------|----------------|
| `brain_first` prompt median | 10 255 → 3 134 tok (−70%) |
| VPS turn p90 (14d) | 107 s → ~20 s |
| Runtime errors 14d | 56+16 → 0 |
| Cost hint | ~€2–5/mo VPS + ~$0.0003 median/LLM call |

---

## Tech debt (honest)

| Item | Value |
|------|-------|
| `core/orchestrator.py` | ~4250 lines |
| `.env.example` keys | ~960 (lab heritage; subset in prod) |

---

## Docs map

| Topic | File |
|-------|------|
| Positioning & FAQ | [docs/HONEST_POSITIONING.md](docs/HONEST_POSITIONING.md) |
| Architecture | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Agent loops | [docs/AGENT_LOOP.md](docs/AGENT_LOOP.md) |
| Memory | [docs/MEMORY.md](docs/MEMORY.md) |
| Security | [SECURITY.md](SECURITY.md) |
| Cursor dev setup | [.cursor/README.md](.cursor/README.md) |

Maintainer rubric scores: [HONEST_POSITIONING.md](docs/HONEST_POSITIONING.md) — self-assessment, not third-party certification.
