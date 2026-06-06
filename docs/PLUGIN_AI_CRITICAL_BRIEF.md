# Plugin AI critical brief

Краткий контракт для LLM при вызове `SelfProgramming.*` и правках в `modules/`.

## Layout

```
modules/<name>/
  module.json    # manifest
  module.py      # class with async execute()
```

## `module.json` (minimum)

| Field | Rule |
|-------|------|
| `name` | snake_case, equals folder name |
| `type` | `module` (or `tool`, `skill`, … — see working plugins in `modules/`) |
| `entrypoint` | `modules.<folder>.module:ClassName` |
| `commands` | optional slash triggers (`/foo` → token `foo`) |
| `capabilities` | intent strings for text routing (must match orchestrator intents) |
| `pip_requirements` | PyPI deps only; runtime does not pip-install |

Reference manifest: pick a small plugin under `modules/` (e.g. `light_reminders`).

## `execute` contract

```python
async def execute(self, args: dict):
    input_data = args.get("input") or {}
    context = args.get("context") or {}
    text = str(input_data.get("payload", ""))
    # return Output or list[Output] from core.models
```

## Routing (why a plugin stays silent)

1. Message starts with `/` → match `commands` in manifests.
2. Plain text → orchestrator intent + first loaded module whose `capabilities` contains that intent.
3. Arbitrary new capability strings do **not** work until core routing knows the intent.

## Safety

- Do not generate modules without an explicit user request.
- No unsafe code; user must know new files appear on the server.
- `/calc` and numeric snippets in dev discussion are not calculator tasks.

Full guide: [PLUGIN_AUTHOR_HANDBOOK_RU.md](PLUGIN_AUTHOR_HANDBOOK_RU.md).
