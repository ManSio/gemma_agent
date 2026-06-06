Work in **gemma_agent**. Follow skill `gemma-agent` and `.cursor/rules/`.

## Task
[describe bug or feature]

## Requirements
1. Trace flow first: input → decision → action → output
2. Read callers and tests **before** editing
3. **Minimal diff** — no drive-by in `orchestrator.py`
4. New env vars → `.env.example` with comment
5. Security: `access_gate`, `safe_paths`, no secrets in logs
6. **Verify** before done: targeted pytest or smoke
7. Report: what was verified, what was not run

## Do not
- Commit unless I ask
- Guess file contents
- Refactor unrelated code
