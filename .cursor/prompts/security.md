Security task in **gemma_agent**. Skill: `gemma-agent`.

## Task
[path traversal / access / logging / injection / export]

## Checklist
- [ ] `core/access_gate.py` — before sensitive handlers (voice STT, private DM)
- [ ] `core/safe_paths.py` — user-supplied paths
- [ ] `core/sensitive_export.py` — audit/export
- [ ] Logs: no tokens, `.env`, full user messages
- [ ] Test or extend existing security test
- [ ] `python scripts/check_public_privacy.py --ci` if logs/export touched

## Flow
Read callers → minimal diff → pytest → report verified / not run.

Do not commit unless I ask.
