# Honest positioning

What this project is, what it is not, and known limits — for humans and tools.

**Short context:** [CHATGPT_PASTE.md](../CHATGPT_PASTE.md) · **Prod metrics:** [PRODUCTION_EVIDENCE_REPORT.md](PRODUCTION_EVIDENCE_REPORT.md)  
**Cursor dev:** [.cursor/README.md](../.cursor/README.md)

---

## Public GitHub date ≠ project age

| Milestone | Date | What it means |
|-----------|------|---------------|
| **Production use** | 2026-05-02 → ongoing | Real Telegram bot, 3–8 users — see [PRODUCTION_EVIDENCE_REPORT.md](PRODUCTION_EVIDENCE_REPORT.md) |
| **Public export** | **2026-06-06** (`Public release: Gemma Agent v3.4.0`) | First commit on public GitHub — **not** “built yesterday” |
| **Private fork** | Years before export | Wide `.env` (~963 keys), large `orchestrator.py` (~4256 lines) = lab heritage |

---

## About the name (`gemma_agent`)

The repository name is **historical** — it refers to the project, not [Google DeepMind Gemma](https://deepmind.google/models/gemma/).

| Sometimes assumed | What we ship |
|-------------------|--------------|
| Google Gemma runtime / local Gemma | **OpenRouter** — GPT, Claude, Gemini, your choice |
| Generic “Gemma agent” tutorial repo | **Telegram bot** for 3–8 users, with tests and CI |
| LangChain / AutoGPT-style framework | Product **orchestrator**, not a reusable agent SDK |

---

## One sentence

**Personal agent platform** for 3–8 trusted users with **two modes:**

| Mode | Default? | Path |
|------|:--------:|------|
| **Assistant** | **yes** | route → LLM → tools as needed → reply |
| **Power Agent** | no (opt-in) | goal runner → tools → self-verify → quality loop |

Not a weekend “AI agent repo”. Not MetaGPT / OpenHands / Claude Code.  
**Intelligence = OpenRouter models.** This repo = **orchestration, memory, guards, ops**.

---

## Self-assessed scores (maintainer — not independent consensus)

Maintainer rubric only — **not** third-party certification.

| Criterion | Score | Notes |
|-----------|:-----:|-------|
| Engineering culture | **9/10** | tests, CI, release_guard, honest security docs |
| Documentation honesty | **9/10** | admits limitations, default vs opt-in |
| Test discipline | **9/10** | high count + behavior contracts (see below) |
| Reliability / ops | **8.5/10** | healers, safe mode, backups |
| Practical utility (3–8 users) | **8.5/10** | |
| Architecture | **8/10** | plugin-bound monolith; orchestrator large |
| Agent-ness (default) | **6/10** | assistant-first by design |
| Agent-ness (power mode) | **7.5/10** | not long-running autonomous coding |
| Memory retrieval (stub) | **4/10** | substring — use Mem0 server |
| Memory retrieval (server) | **6–7/10** | API search + merge |
| Innovation (algorithms) | **5/10** | orchestrator, not new foundation model |
| Over-engineering risk | **7/10** | wide surface; default path stays narrow |
| Community / adoption | **2/10** | intentional niche; not a growth product |

**Strength:** implementation quality and operability — not fundamental AI research.

---

## What reviewers can verify vs must trust

| Verifiable in git (no prod access) | Trust on word (privacy) |
|------------------------------------|-------------------------|
| 2580+ pytest, CI, `release_guard` | `turns.jsonl` / `llm_usage.jsonl` raw rows |
| `wc -l core/orchestrator.py` (~4256) | Exact €/month on operator VPS |
| `grep -c '=' .env.example` (~963 keys) | “Users felt pain” on a given day |
| SECURITY.md, CONTRIBUTING.md content | Independent security audit |

**pytest ≠ Telegram:** 20 May 2026 parallel-DM bug shipped with green CI — documented in [PRODUCTION_EVIDENCE_REPORT.md](PRODUCTION_EVIDENCE_REPORT.md) §10.

```bash
git clone https://github.com/ManSio/gemma_agent.git && cd gemma_agent
python scripts/print_repo_stats.py
python -m pytest tests/ --collect-only -q | tail -1
wc -l core/orchestrator.py
grep -cE '^[A-Z_][A-Z0-9_]*=' .env.example
```

---

## 1. Over-engineering?

**Fair concern.** Codebase is wide (voice, vision, healers, learning, agent mode) because the private fork evolved over years. Public build **strips** spatial, MCE prod, 47 dormant plugins.

| Every message (default) | Exists but off unless enabled |
|-------------------------|-------------------------------|
| `input_layer` → `orchestrator` → `brain/pipeline` | `goal_runner` |
| Plugin tools | `turn_quality_loop` |
| Context compression | `self_verify_pass` every send |
| Flood / access gate | Full autonomy stacks |

~**80%** of traffic = cheap assistant path. See [AGENT_LOOP.md](AGENT_LOOP.md).

---

## 2. Terminology map (not marketing)

| Term | Code | Data |
|------|------|------|
| **STM** | `core/behavior_store.py` | `data/behavior/*.json` |
| **MTM** | `core/context_compression.py`, compactor | trimmed / summarized working context |
| **LTM** | `core/mem0_memory/mem0_module.py` | Mem0 API or encrypted stub |

Details + **retrieval path:** [MEMORY.md](MEMORY.md#retrieval-how-memory-enters-the-llm-prompt)

---

## 3. Self-healing — what it is and is not

| Real | Not claimed |
|------|-------------|
| `ModuleFailureHealer` — fail count → disable module | Kubernetes auto-heal |
| `resilience_controller` — safe mode allowlist | Magic recovery from any bug |
| `llm_transient_recovery` — retry / model fallback | (yes, includes retry layer) |

Details: [SELF_HEALING.md](SELF_HEALING.md)

---

## 4. OpenRouter dependency

Removing OpenRouter and using a weak local model **will** drop perceived quality. Project value is in:

- routing (`core/orchestrator.py`, `pipeline_routing.py`)
- context budget (`context_budget.py`)
- memory assembly (`memory_recall_facade.py`, `prompt_pack.py`)
- guards (`prompt_injection_guard.py`, honest refusal)
- test-locked behavior (2580+ cases)

Review question: **“How good is orchestration?”** — not **“How smart is Gemma?”**

---

## 5. Agent vs assistant vs coding agents

| | Gemma (default) | Gemma (power) | MetaGPT / OpenHands |
|--|-----------------|---------------|---------------------|
| UI | Telegram | Telegram | IDE / terminal |
| Multi-step goals | rare | `goal_runner` | core design |
| Coding loop | docs/RAG | + verify | edit → test → iterate |
| Target | 3–8 trusted users | same | dev teams |

Enable power mode: `python scripts/apply_power_agent_env.py` — [AGENT_LOOP.md](AGENT_LOOP.md)

---

## 6. Test count ≠ test quality

**2580+** cases is verifiable (`python scripts/print_repo_stats.py`). Suite is **mixed**:

| Layer | Examples | Locks |
|-------|----------|-------|
| Product UX | `test_product_behavior.py` | pivot, search gates |
| Multi-turn | `test_pending_flow.py` | interrupt / pending cleanup |
| Memory | `test_mem0_merge.py` | dedup, merge ranking |
| Honesty | `test_acc11_honest_refusal.py` | no fake sources |
| Routing | `test_orchestrator_intent_routing.py` | intent → module |
| Release | **90** files in `release_guard.py` | anti-regression on ship |

We admit: many unit tests on helpers too. **release_guard** targets product behavior.

Details: [developer-guide/testing.md](developer-guide/testing.md) · [ACCEPTANCE_CRITERIA.md](ACCEPTANCE_CRITERIA.md)

---

## 7. Orchestrator complexity (tech debt honesty)

| Fact | Value |
|------|-------|
| `core/orchestrator.py` | ~4250 lines |
| `.env.example` keys | ~960 (lab heritage; subset used in prod) |
| Mitigation | plugins, tests on routing, narrow default path |

We do **not** claim a small elegant core. Claim: **long-lived tested monolith** exported to public with scope trimmed.

---

## 8. Memory retrieval (the hard question)

Storage tiers exist in many projects. **Retrieval** is what matters:

```
STM: recent_dialogue (behavior_store, paired trim)
  ↓
LTM: get_memory().on_before_response() → mem0_facts
  ↓
MTM: memory_recall_facade.build_pipeline_memory_addon()
  ↓
Prompt: prompt_pack.py → memory_facts, dialogue_summary, user_facts
  ↓
Cap: context_budget + context_compression
```

Key files: `core/brain/pipeline.py` (~376), `core/memory_recall_facade.py`, `core/brain/prompt_pack.py`

**Stub weakness:** substring match — documented. **Server:** search score + `_merge_search_payloads`.

---

## Related docs

| Topic | Doc |
|-------|-----|
| Cursor | [.cursor/README.md](../.cursor/README.md) |
| Agent loops | [AGENT_LOOP.md](AGENT_LOOP.md) |
| Memory tiers + retrieval | [MEMORY.md](MEMORY.md) |
| Architecture | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Tests | [CI.md](CI.md), [testing.md](developer-guide/testing.md) |
| AI reviewers | [AGENTS.md](../AGENTS.md) |
