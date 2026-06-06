#!/usr/bin/env python3
"""
Apply POWER_AGENT profile to working .env (opt-in fuller agent loop).

See config/power_agent.env.fragment and docs/AGENT_LOOP.md.

  python scripts/apply_power_agent_env.py
  python scripts/apply_power_agent_env.py /opt/gemma_agent
"""
from __future__ import annotations

import sys
from pathlib import Path

# Keys from config/power_agent.env.fragment (non-comment lines)
_FORCE: dict[str, str] = {
    "GOAL_RUNNER_ENABLED": "true",
    "GOAL_RUNNER_EXECUTOR_MODE": "true",
    "GOAL_RUNNER_AUTO_START": "true",
    "GOAL_RUNNER_AUTO_START_SMART": "true",
    "GOAL_RUNNER_PLAN_VALIDATOR": "true",
    "GOAL_RUNNER_TELEGRAM_PROGRESS": "true",
    "SELF_VERIFY_ACTIVE": "true",
    "REFLECTION_HEAVY_ENABLED": "true",
    "TURN_QUALITY_LOOP_ENABLED": "true",
    "TURN_QUALITY_LESSON_DRAFT": "true",
    "TURN_QUALITY_SCAN_ON_TICK": "true",
    "HEALERS_ENABLED": "true",
    "RESILIENCE_AUTONOMY_ENABLED": "true",
    "GOAL_ENGINE_ENABLED": "true",
    "MCE_ENABLED": "false",
    "MCE_AUTO_APPLY": "false",
    "MCE_EXPERIMENT_ENABLED": "false",
    "MEM0_API_URL": "http://127.0.0.1:8001",
    "MEM0_API_PREFIX": "v3",
}

_MARKER = "# --- POWER_AGENT (apply_power_agent_env.py) ---"


def _patch_env(path: Path) -> list[str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    seen: set[str] = set()
    out: list[str] = []
    skip_block = False
    for line in lines:
        if line.strip() == _MARKER:
            skip_block = True
            continue
        if skip_block:
            if line.startswith("# --- END POWER_AGENT"):
                skip_block = False
            continue
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
        out.append(_MARKER)
        out.append("# Fuller agent loop — docs/AGENT_LOOP.md")
        for k in missing:
            out.append(f"{k}={_FORCE[k]}")
        out.append("# --- END POWER_AGENT ---")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return list(_FORCE.keys())


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
    env_path = root / ".env"
    try:
        keys = _patch_env(env_path)
    except FileNotFoundError:
        print(f"[ERR] no .env at {env_path} — run: cp .env.example .env")
        return 1
    print(f"[OK] {env_path}: POWER_AGENT profile applied ({len(keys)} keys)")
    print("Next: set GEMMA_MEM0_USE_STUB=false in scripts/gemma_panel.local.conf for semantic Mem0")
    print("      bash scripts/gemma_panel.sh restart-all")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
