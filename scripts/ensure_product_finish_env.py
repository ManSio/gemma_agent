#!/usr/bin/env python3
"""Добавить в .env недостающие ключи PRODUCT_FINISH (без перезаписи существующих)."""
from __future__ import annotations

import sys
from pathlib import Path

# Ключи, которые при --apply-prod снимают false→true (уже есть в .env)
_PROD_TRUE_KEYS = frozenset(
    {
        "BRAIN_DIRECT_DIALOG_ENABLED",
        "BRAIN_CHAT_AGENT_MODE",
        "BRAIN_CHAT_AGENT_USE_MAIN_MODEL",
        "CONTEXT_TOOL_OUTPUT_TRIM_ENABLED",
        "REFLECTION_HEAVY_ENABLED",
        "TURN_OBSERVER_ENABLED",
        "BRAIN_CHAT_CONTEXT_SLIM",
        "BRAIN_OPERATOR_CORRECTIONS_IN_HINT",
        "GEO_MAPS_ENABLED",
    }
)

BLOCK = """
# --- PRODUCT_FINISH (закрытие плана 2026-05-22) ---
MCE_AUTO_APPLY=false
MCE_EXPERIMENT_ENABLED=false
GOAL_RUNNER_AUTO_START=false
GOAL_RUNNER_AUTO_START_SMART=false
ROUTER_PASSIVE_ENABLED=false
LLM_TRIAGE_ENABLED=false
ROUTE_RISK_CLUSTER_AUTO_LESSON=false
ROUTE_RISK_RECORD_CLARIFY=false
TURN_QUALITY_LOOP_ENABLED=false
TURN_QUALITY_AUTO_PENDING_CORRECTION=false
TELEGRAM_PIPELINE_PRIVATE_PARALLEL=1
TURN_OBSERVER_ENABLED=true
CONTEXT_TOOL_OUTPUT_TRIM_ENABLED=true
CONTEXT_TOOL_OUTPUT_KEEP_RECENT=2
CONTEXT_TOOL_OUTPUT_MIN_CHARS=1200
REFLECTION_HEAVY_ENABLED=true
BRAIN_DIRECT_DIALOG_ENABLED=true
BRAIN_CHAT_AGENT_MODE=true
BRAIN_CHAT_AGENT_USE_MAIN_MODEL=true
BRAIN_DIRECT_DIALOG_RECENT_TURNS=10
BRAIN_DIRECT_DIALOG_MAX_TOKENS=1024
BRAIN_STANDARD_RECENT_COUNT=10
BRAIN_CHAT_CONTEXT_SLIM=true
BRAIN_OPERATOR_CORRECTIONS_IN_HINT=true
BRAIN_NEWS_DIRECT_FROM_SEARCH=false
BRAIN_LLM_FREE_MODEL=deepseek/deepseek-v4-flash
BRAIN_FAST_CHITCHAT_MODEL=deepseek/deepseek-v4-flash
BRAIN_LLM_PREMIUM_MODEL=deepseek/deepseek-v4-pro
RESOURCE_METRICS_BOOT_DELAY_SEC=45
NEWS_RSS_MAX_ITEMS=12
NEWS_DIRECT_MAX_ITEMS=12
GEO_MAPS_ENABLED=true
# GEMMA_TURNS_LOG_PATH=data/runtime/turns.jsonl
""".strip()


def existing_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    if not path.is_file():
        return keys
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        keys.add(s.split("=", 1)[0].strip())
    return keys


def _patch_prod_flags(env_path: Path) -> list[str]:
    """Подтянуть false→true для флагов PRODUCT_FINISH, если ключ уже в файле."""
    if not env_path.is_file():
        return []
    lines = env_path.read_text(encoding="utf-8", errors="replace").splitlines()
    patched: list[str] = []
    out: list[str] = []
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            key, _, val = s.partition("=")
            key = key.strip()
            if key in _PROD_TRUE_KEYS and val.strip().lower() in ("false", "0", "no", "off"):
                out.append(f"{key}=true")
                patched.append(key)
                continue
        out.append(line)
    if patched:
        env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return patched


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
    apply_prod = "--apply-prod" in sys.argv
    env_path = root / ".env"
    if apply_prod:
        patched = _patch_prod_flags(env_path)
        if patched:
            print(f"[OK] {env_path}: обновлено → true: {', '.join(patched)}")
    have = existing_keys(env_path)
    to_add: list[str] = []
    for line in BLOCK.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            to_add.append(line)
            continue
        key = s.split("=", 1)[0].strip()
        if key not in have:
            to_add.append(line)
    if not any("=" in ln for ln in to_add):
        if not apply_prod or not patched:
            print(f"[OK] {env_path}: все ключи уже есть")
        return 0
    with open(env_path, "a", encoding="utf-8") as f:
        f.write("\n\n" + "\n".join(to_add) + "\n")
    added = [
        ln.split("=", 1)[0].strip()
        for ln in to_add
        if "=" in ln and not ln.strip().startswith("#")
    ]
    print(f"[OK] {env_path}: добавлено {len(added)} ключ(ей): {', '.join(added)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
