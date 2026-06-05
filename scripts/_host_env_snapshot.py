#!/usr/bin/env python3
import json
import hashlib
import urllib.request
from pathlib import Path

def load_env():
    d = {}
    for ln in Path(".env").read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in ln and not ln.strip().startswith("#"):
            k, _, v = ln.partition("=")
            d[k.strip()] = v.strip()
    return d

def bot_me(token):
    if not token:
        return {}
    r = json.loads(urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getMe", timeout=15).read())
    return r.get("result") or {}

e = load_env()
prefixes = ("NEWS_", "BRAIN_OWN", "BRAIN_NEWS", "ROUTER_", "SEARX", "OPENROUTER_MODEL", "BRAIN_STANDARD", "TELEGRAM_PIPELINE", "OPENROUTER_API", "MEM0_", "GOAL_", "MCE_", "LLM_TRIAGE", "HEALERS", "BRAIN_KV", "BRAIN_CHAT", "BRAIN_LLM")
out = {
    "env_hashes": {k: hashlib.sha256(v.encode()).hexdigest()[:16] for k, v in sorted(e.items())},
    "bot": bot_me(e.get("TELEGRAM_TOKEN", "")),
    "openrouter_key_len": len(e.get("OPENROUTER_API_KEY", "")),
    "routing": {k: e.get(k, "") for k in sorted(e) if k.startswith(prefixes)},
}
print(json.dumps(out, ensure_ascii=False))
