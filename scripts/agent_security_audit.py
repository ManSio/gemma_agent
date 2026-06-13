#!/usr/bin/env python3
"""
Honest security / privacy audit for Gemma Agent (public build).

  python scripts/agent_security_audit.py
  python scripts/agent_security_audit.py --quick   # skip pytest
  python scripts/agent_security_audit.py --ci      # GitHub Actions (no .env required)
  python scripts/agent_security_audit.py --json

Does NOT claim "military-grade" — reports what is checked and what is not.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# Patterns that must not appear in git-tracked sources (except blocklist files)
SECRET_PATTERNS = [
    ("telegram_token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b")),
    ("openrouter_sk", re.compile(r"\bsk-or-v1-[A-Za-z0-9]{20,}\b")),
    ("bearer_jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")),
]

RECOMMENDATIONS = [
    "Keep .env chmod 600; never commit secrets.",
    "Run this audit before each release: python scripts/agent_security_audit.py",
    "Production: USER_ACCESS_APPROVAL_REQUIRED=true, narrow ADMIN_USER_IDS.",
    "Use real Mem0 or cloud with auth — not stub — if memory must be isolated.",
    "Rotate provider tokens if ever leaked in chat or git.",
]

LIMITATIONS = [
    "LLM output is not cryptographically verified — prompt injection remains possible.",
    "Mem0 stub stores memories in plain JSON on disk — not for multi-tenant production.",
    "security_layer encrypts optional tool payloads; chat content is not E2E encrypted.",
    "USER_ACCESS_APPROVAL_REQUIRED=false opens bot to anyone with the Telegram link.",
    "Admin commands (/admin_*) require ADMIN_USER_IDS — misconfiguration = privilege risk.",
    "SearXNG queries leave your network — configure engines and rate limits.",
    "Voice STT/TTS may send audio to OpenRouter/OpenAI if cloud backends enabled.",
]


def _run(cmd: List[str], timeout: int = 300) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        return r.returncode == 0, out[-2000:]
    except Exception as e:
        return False, type(e).__name__


def check_env_not_tracked() -> Dict[str, Any]:
    ok = True
    notes: List[str] = []
    for name in (".env", "config/agent_telegram.env"):
        p = ROOT / name
        if p.exists():
            tracked, _ = _run(["git", "ls-files", "--error-unmatch", name])
            if tracked:
                ok = False
                notes.append(f"{name} is git-tracked — must be in .gitignore only")
            else:
                notes.append(f"{name} exists locally (not in git) — OK")
        else:
            notes.append(f"{name} absent — OK for fresh clone")
    return {"ok": ok, "notes": notes}


def check_privacy_scan() -> Dict[str, Any]:
    ok, out = _run([sys.executable, str(ROOT / "scripts/check_public_privacy.py"), "--ci"])
    return {"ok": ok, "detail": out.splitlines()[-3:] if out else []}


def check_dotenv_permissions() -> Dict[str, Any]:
    p = ROOT / ".env"
    if not p.exists():
        return {"ok": True, "notes": [".env missing — fill before prod"]}
    if sys.platform == "win32":
        return {"ok": True, "notes": [".env on Windows — set ACL to current user only if shared PC"]}
    try:
        mode = p.stat().st_mode & 0o777
        if mode & 0o077:
            return {"ok": False, "notes": [f".env mode {oct(mode)} — recommend chmod 600"]}
        return {"ok": True, "notes": [f".env mode {oct(mode)} — OK"]}
    except OSError as e:
        return {"ok": False, "notes": [str(e)]}


def check_required_secrets_set(*, ci: bool = False) -> Dict[str, Any]:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
    except ImportError:
        pass
    env_path = ROOT / ".env"
    if ci and not env_path.is_file():
        return {
            "ok": True,
            "skipped": True,
            "notes": [".env absent — skipped in CI (fresh clone); configure before prod"],
        }

    missing = []
    for key in ("TELEGRAM_TOKEN", "OPENROUTER_API_KEY"):
        if not (os.getenv(key) or "").strip():
            missing.append(key)
    missing_count = len(missing)
    admin = (os.getenv("ADMIN_USER_IDS") or "").strip()
    guest_open = os.getenv("USER_ACCESS_APPROVAL_REQUIRED", "true").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }
    notes = []
    if missing_count:
        notes.append(f"Required provider keys unset in .env ({missing_count})")
    if not admin:
        notes.append("ADMIN_USER_IDS empty — admin commands disabled")
    if guest_open:
        notes.append("USER_ACCESS_APPROVAL_REQUIRED=false — open access to any Telegram user")
    return {"ok": missing_count == 0, "notes": notes, "open_access": guest_open}


def check_security_tests(quick: bool) -> Dict[str, Any]:
    if quick:
        return {"ok": True, "skipped": True}
    ok, out = _run(
        [sys.executable, "-m", "pytest", "tests/test_security_layer.py", "-q", "--tb=no"],
        timeout=120,
    )
    return {"ok": ok, "detail": out.splitlines()[-5:] if out else []}


def check_release_guard_smoke() -> Dict[str, Any]:
    ok, out = _run([sys.executable, str(ROOT / "scripts/release_guard.py"), "--smoke"], timeout=180)
    return {"ok": ok, "detail": out.splitlines()[-5:] if out else []}


def build_report(*, quick: bool, ci: bool = False) -> Dict[str, Any]:
    checks = {
        "env_not_tracked": check_env_not_tracked(),
        "dotenv_permissions": check_dotenv_permissions(),
        "privacy_scan": check_privacy_scan(),
        "secrets_configured": check_required_secrets_set(ci=ci),
        "security_layer_tests": check_security_tests(quick),
        "release_guard_smoke": check_release_guard_smoke() if not quick else {"ok": True, "skipped": True},
    }
    failed = [k for k, v in checks.items() if not v.get("ok", False) and not v.get("skipped")]
    return {
        "root": str(ROOT),
        "passed": len(failed) == 0,
        "failed_checks": failed,
        "checks": checks,
        "limitations": LIMITATIONS,
        "recommendations": list(RECOMMENDATIONS),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="Skip pytest and release_guard")
    ap.add_argument("--ci", action="store_true", help="CI mode: no .env required on fresh clone")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    report = build_report(quick=args.quick, ci=args.ci)

    from core.sensitive_export import security_audit_public_report

    pub = security_audit_public_report(report)
    if args.json:
        print(json.dumps(pub, ensure_ascii=False, indent=2))
    else:
        print("=== Gemma Agent security audit (honest) ===")
        print("Root: gemma_agent (public build)")
        for name, data in pub["checks"].items():
            mark = "OK" if data.get("ok") or data.get("skipped") else "FAIL"
            print(f"\n[{mark}] {name}")
            note_n = len(data.get("notes") or [])
            if note_n:
                print(f"  - notes={note_n} (use --json for detail)")
            if data.get("skipped"):
                print("  - skipped")
        print("\n--- Known limitations (not bugs) ---")
        for line in LIMITATIONS:
            print(f"  - {line}")
        print("\n--- Recommendations ---")
        for line in RECOMMENDATIONS:
            print(f"  - {line}")
        print()
        print("PASS" if report["passed"] else "FAIL — fix items above")

    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
