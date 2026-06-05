# Architecture

## One paragraph

Telegram message: **input → security → orchestrator → brain (OpenRouter) → guards → Telegram**. Most features are plugins; typical chat goes `chat_orchestrator` → `call_brain` in `core/brain/pipeline.py`.

```
Telegram → input_layer → orchestrator.plan (pre_llm_plan)
        → orchestrator.execute → chat_orchestrator → pipeline.call_brain
        → response_adapter → Telegram
```

## Directory map

| Path | Role |
|------|------|
| `main.py` | Entry, plugin load, polling/webhook |
| `core/input_layer.py` | Telegram ingress, locks |
| `core/orchestrator.py` | plan + execute |
| `core/brain/pipeline.py` | Single LLM turn |
| `core/pre_llm_plan.py` | Answers without OpenRouter |
| `modules/` | 19 plugins (`module.json` each) |
| `config/modules_catalog.json` | Tier A/B catalog |
| `data/` | Runtime state (gitignored) |
| `scripts/` | ops, tests, bootstrap |

## Data contracts

| Object | Module |
|--------|--------|
| Input | `core.models.Input` |
| Plan | `core.models.Plan` |
| Output | `core.models.Output` |

## Logs map

| Need | File |
|------|------|
| Turn metadata | `data/runtime/turns.jsonl` |
| Full dialogue | `data/users/behavior/<id>__dm.json` |
| Errors | `data/runtime_errors.jsonl` |

## Diagram

![Pipeline](../assets/pipeline-overview.svg)
