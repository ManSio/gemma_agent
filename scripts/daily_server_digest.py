#!/usr/bin/env python3
"""
Суточный дайджест с серверов: turns, errors, llm_usage, архивы.

  python scripts/daily_server_digest.py --days 1
  python scripts/daily_server_digest.py --remote  # SSH HOST_LAN + VPS (см. OPS_PRIVATE)

Пишет: data/benchmarks/daily_digest_YYYYMMDD.json и docs/archive/DAILY_OPS_YYYY-MM-DD_RU.md
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _ssh_target(alias: str) -> str:
    priv = _ROOT / "docs" / "OPS_PRIVATE.local.md"
    key = f"{alias}_SSH"
    if priv.is_file():
        for line in priv.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    return alias


def _audit_remote(host_label: str, ssh: str, days: int) -> dict:
    remote_json = f"/tmp/audit_{host_label}.json"
    cmd = (
        f'ssh -o ConnectTimeout=12 {ssh} '
        f'"cd /opt/gemma_agent && venv/bin/python3 scripts/server_full_audit.py '
        f'--host-label {host_label} --days {days} --json-out {remote_json}"'
    )
    subprocess.run(cmd, shell=True, check=True)
    raw = subprocess.check_output(f"ssh {ssh} cat {remote_json}", shell=True)
    doc = json.loads(raw.decode("utf-8"))
    return (doc.get("hosts") or [{}])[0]


def _audit_local(root: Path, host_label: str, days: int) -> dict:
    from scripts.server_full_audit import audit_host

    return audit_host(root, host_label=host_label, days=days)


def _render_md(hosts: list, *, stamp: str) -> str:
    lines = [f"# Суточный ops-дайджест ({stamp})", ""]
    for host in hosts:
        if not isinstance(host, dict):
            continue
        turns = host.get("turns") if isinstance(host.get("turns"), dict) else {}
        archives = host.get("archives") if isinstance(host.get("archives"), dict) else {}
        errors = host.get("errors") if isinstance(host.get("errors"), dict) else {}
        lines += [
            f"## {str(host.get('host') or '?')[:64]} (`{str(host.get('git_head') or '')[:160]}`)",
            "",
            f"- turns (window): **{int(turns.get('count') or 0)}**",
            f"- latency p50: **{turns.get('latency_ms_p50')}** ms, p90: **{turns.get('latency_ms_p90')}** ms",
            f"- outcomes: {turns.get('outcomes') if isinstance(turns.get('outcomes'), dict) else {}}",
            f"- issues: {turns.get('issues_top') if isinstance(turns.get('issues_top'), list) else []}",
            f"- incomplete (excerpt heuristic): **{int(turns.get('suspect_incomplete_excerpt') or 0)}**",
            f"- long Q / short A: **{int(turns.get('long_q_short_a') or 0)}**",
            f"- turns with brain_recent_limit: **{int(turns.get('with_brain_recent_limit') or 0)}**",
            f"- archive leaks: **{int(archives.get('leaks') or 0)}** / msgs {int(archives.get('messages') or 0)}",
            f"- errors (window): **{int(errors.get('count') or 0)}** top {errors.get('top') if isinstance(errors.get('top'), list) else []}",
            "",
        ]
    return "\n".join(lines)


def _hosts_for_md(hosts: list) -> list:
    """Strict allowlist for markdown output to avoid persisting secret-like content."""
    out: list = []
    for h in hosts:
        if not isinstance(h, dict):
            continue
        turns = h.get("turns") if isinstance(h.get("turns"), dict) else {}
        archives = h.get("archives") if isinstance(h.get("archives"), dict) else {}
        errors = h.get("errors") if isinstance(h.get("errors"), dict) else {}
        host_row = {
            "host": str(h.get("host") or "?")[:64],
            "git_head": str(h.get("git_head") or "")[:160],
            "turns": {
                "count": int(turns.get("count") or 0),
                "latency_ms_p50": turns.get("latency_ms_p50"),
                "latency_ms_p90": turns.get("latency_ms_p90"),
                "outcomes": turns.get("outcomes") if isinstance(turns.get("outcomes"), dict) else {},
                "issues_top": turns.get("issues_top") if isinstance(turns.get("issues_top"), list) else [],
                "suspect_incomplete_excerpt": int(turns.get("suspect_incomplete_excerpt") or 0),
                "long_q_short_a": int(turns.get("long_q_short_a") or 0),
                "with_brain_recent_limit": int(turns.get("with_brain_recent_limit") or 0),
                "samples_incomplete": [],
            },
            "archives": {
                "leaks": int(archives.get("leaks") or 0),
                "messages": int(archives.get("messages") or 0),
            },
            "errors": {
                "count": int(errors.get("count") or 0),
                "top": errors.get("top") if isinstance(errors.get("top"), list) else [],
            },
        }
        out.append(host_row)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(_ROOT))
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--remote", action="store_true", help="Снять с HOST_LAN и VPS_PROD по SSH")
    ap.add_argument("--md-out", default="")
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    hosts: list = []
    if args.remote:
        for label in ("HOST_LAN", "VPS_PROD"):
            try:
                hosts.append(_audit_remote(label, _ssh_target(label), args.days))
            except Exception as e:
                hosts.append({"host": label, "error_type": type(e).__name__})
    else:
        hosts.append(_audit_local(root, "local", args.days))

    from core.sensitive_export import audit_document_public

    out_doc = {"ts": datetime.now(timezone.utc).isoformat(), "stamp": stamp, "hosts": hosts}
    safe_doc = audit_document_public(out_doc)
    json_path = Path(args.json_out or root / "data/benchmarks" / f"daily_digest_{stamp.replace('-', '')}.json")
    md_path = Path(args.md_out or root / "docs" / "archive" / f"DAILY_OPS_{stamp}_RU.md")
    md_hosts = _hosts_for_md(safe_doc.get("hosts") or [])
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(safe_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_md(md_hosts, stamp=stamp), encoding="utf-8")
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
