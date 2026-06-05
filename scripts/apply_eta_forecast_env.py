#!/usr/bin/env python3
"""Принудительно выставить env для ETC/ETA прогноза (аудит VPS 05/2026)."""
from __future__ import annotations

import sys
from pathlib import Path

_FORCE: dict[str, str] = {
    "BRAIN_LLM_TOKENS_PER_SEC_EST": "52",
    "BRAIN_LLM_ETA_OVERHEAD_SEC": "1.8",
    "BRAIN_LLM_ETA_MAX_TOK_FRAC": "0.28",
    "BRAIN_LLM_ETA_SHORT_GEN_TOKENS": "220",
    "BRAIN_LLM_ETA_PROMPT_TPS": "1400",
    "BRAIN_LLM_ETA_PROMPT_CHARS_PER_TOK": "4.2",
    "BRAIN_LLM_ETA_MIN_GEN_TOKENS": "140",
    "BRAIN_LLM_ETA_SHORT_USER_CHARS": "80",
    "BRAIN_LLM_ETA_ASSEMBLY_LEARN_ENABLED": "true",
    "BRAIN_LLM_ETA_ASSEMBLY_LEARN_MIN_SAMPLES": "3",
    "TELEGRAM_PROGRESS_ASSEMBLY_BUFFER_SEC": "10",
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
        out.append("# --- apply_eta_forecast_env (forced) ---")
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
    print(f"[OK] {env_path}: ETA forecast {len(keys)} ключ(ей)")
    for k in sorted(keys):
        print(f"  {k}={_FORCE[k]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
