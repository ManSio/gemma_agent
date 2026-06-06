#!/usr/bin/env python3
"""
Принудительно выставить PERSONAL_PROD env на сервере (перезапись значений, не только append).

См. gemma-owner.mdc, scripts/ensure_product_finish_env.py, docs/AGENT_SUBSYSTEMS_MAP_RU.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ключ → значение для узкого круга в проде (шумовые автономии выкл, обучение через 👎 + hint).
_FORCE: dict[str, str] = {
    "MCE_AUTO_APPLY": "false",
    "MCE_EXPERIMENT_ENABLED": "false",
    "MCE_ENABLED": "false",
    "GOAL_RUNNER_AUTO_START": "false",
    "GOAL_RUNNER_AUTO_START_SMART": "false",
    "GOAL_RUNNER_ENABLED": "false",
    "GOAL_RUNNER_EXECUTOR_MODE": "false",
    "HEALERS_ENABLED": "true",
    "HEALERS_ENV_MUTATION_ENABLED": "false",
    "ROUTER_PASSIVE_ENABLED": "false",
    "LLM_TRIAGE_ENABLED": "false",
    "ROUTE_RISK_CLUSTER_AUTO_LESSON": "false",
    "ROUTE_RISK_RECORD_CLARIFY": "false",
    "TURN_QUALITY_LOOP_ENABLED": "false",
    "TURN_QUALITY_AUTO_PENDING_CORRECTION": "false",
    "TURN_QUALITY_SCAN_ON_TICK": "false",
    "TELEGRAM_PIPELINE_PRIVATE_PARALLEL": "1",
    "BRAIN_OPERATOR_CORRECTIONS_IN_HINT": "true",
    "BRAIN_CHAT_CONTEXT_SLIM": "true",
    "CDC_ENGINE_ENABLED": "false",
    "AGENT_KV_ENABLED": "true",
    "SESSION_META_RECALL_ENABLED": "true",
    "DIALOG_RECALL_NL_ROUTE_ENABLED": "true",
}


def _patch_env(path: Path) -> list[str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            key = s.split("=", 1)[0].strip()
            if key in _FORCE:
                out.append(f"{key}={_FORCE[key]}")
                seen.add(key)
                continue
        out.append(line)
    missing = [k for k in _FORCE if k not in seen]
    if missing:
        out.append("")
        out.append("# --- apply_personal_prod_env (forced) ---")
        for k in missing:
            out.append(f"{k}={_FORCE[k]}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return list(_FORCE.keys())


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
    env_path = root / ".env"
    try:
        keys = _patch_env(env_path)
    except FileNotFoundError:
        print(f"[ERR] нет файла {env_path}")
        return 1
    print(f"[OK] {env_path}: принудительно {len(keys)} ключ(ей) PERSONAL_PROD")
    for k in sorted(keys):
        print(f"  {k}={_FORCE[k]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
