#!/usr/bin/env python3
"""
Суточный дайджест с серверов: turns, errors, llm_usage, архивы.

  python scripts/daily_server_digest.py --days 1
  python scripts/daily_server_digest.py --date 2026-06-06
  python scripts/daily_server_digest.py --backfill-from 2026-06-05 --backfill-to 2026-06-13
  python scripts/daily_server_digest.py --remote  # SSH HOST_LAN + VPS (см. OPS_PRIVATE)

Пишет: data/benchmarks/daily_digest_YYYYMMDD.json и docs/archive/DAILY_OPS_YYYY-MM-DD_RU.md
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
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


def _audit_remote(host_label: str, ssh: str, *, day: str = "", days: int = 1) -> dict:
    remote_json = f"/tmp/audit_{host_label}_{day or 'rolling'}.json"
    date_arg = f" --date {day}" if day else f" --days {days}"
    cmd = (
        f'ssh -o ConnectTimeout=12 {ssh} '
        f'"cd /srv/gemma_bot 2>/dev/null || cd /opt/gemma_agent; '
        f'venv/bin/python3 scripts/server_full_audit.py '
        f'--host-label {host_label}{date_arg} --json-out {remote_json}"'
    )
    subprocess.run(cmd, shell=True, check=True)
    raw = subprocess.check_output(f"ssh {ssh} cat {remote_json}", shell=True)
    doc = json.loads(raw.decode("utf-8"))
    return (doc.get("hosts") or [{}])[0]


def _audit_local(root: Path, host_label: str, *, day: str = "", days: int = 1) -> dict:
    from scripts.server_full_audit import audit_host

    return audit_host(root, host_label=host_label, days=days, day=day)


def _iter_days(start: str, end: str):
    """Yield UTC calendar days from start to end inclusive."""
    a = datetime.strptime(start[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    b = datetime.strptime(end[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if b < a:
        a, b = b, a
    cur = a
    while cur <= b:
        yield cur.strftime("%Y-%m-%d")
        cur += timedelta(days=1)


def _write_digest(
    root: Path,
    stamp: str,
    hosts: list,
    host_labels: list[str],
    *,
    backfill_note: str = "",
    json_out: str = "",
    md_out: str = "",
) -> None:
    from core.sensitive_export import (
        write_audit_document_json,
        write_audit_document_md,
    )

    out_doc = {"ts": datetime.now(timezone.utc).isoformat(), "stamp": stamp, "hosts": hosts}
    json_path = Path(json_out or root / "data/benchmarks" / f"daily_digest_{stamp.replace('-', '')}.json")
    md_path = Path(md_out or root / "docs/archive" / f"DAILY_OPS_{stamp}_RU.md")
    epoch = int(time.time())
    labels = tuple(host_labels)
    write_audit_document_json(
        json_path,
        out_doc,
        host_labels=labels,
        stamp_day=stamp,
        exported_at_epoch=epoch,
    )
    write_audit_document_md(
        md_path,
        out_doc,
        host_labels=labels,
        stamp_day=stamp,
        exported_at_epoch=epoch,
    )
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")


def _collect_hosts(root: Path, *, remote: bool, day: str, days: int) -> tuple[list, list[str]]:
    hosts: list = []
    host_labels: list[str] = []
    if remote:
        for label in ("HOST_LAN", "VPS_PROD"):
            try:
                hosts.append(_audit_remote(label, _ssh_target(label), day=day, days=days))
                host_labels.append(label)
            except Exception as e:
                hosts.append({"host": label, "error_type": type(e).__name__})
                host_labels.append(label)
    else:
        label = "VPS_PROD" if (root / "data/runtime/turns.jsonl").is_file() else "local"
        hosts.append(_audit_local(root, label, day=day, days=days))
        host_labels.append(label)
    return hosts, host_labels


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(_ROOT))
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--date", default="", help="UTC day YYYY-MM-DD")
    ap.add_argument("--backfill-from", default="", help="Backfill start day YYYY-MM-DD")
    ap.add_argument("--backfill-to", default="", help="Backfill end day YYYY-MM-DD")
    ap.add_argument("--remote", action="store_true", help="Снять с HOST_LAN и VPS_PROD по SSH")
    ap.add_argument("--md-out", default="")
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()
    root = Path(args.root).resolve()

    if args.backfill_from:
        end = args.backfill_to or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        note = f"Backfill с VPS {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC"
        for stamp in _iter_days(args.backfill_from, end):
            hosts, labels = _collect_hosts(root, remote=args.remote, day=stamp, days=1)
            _write_digest(root, stamp, hosts, labels, backfill_note=note)
        return 0

    stamp = str(args.date or "").strip()[:10] or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    hosts, labels = _collect_hosts(
        root,
        remote=args.remote,
        day=stamp if args.date else "",
        days=args.days,
    )
    _write_digest(
        root,
        stamp,
        hosts,
        labels,
        json_out=args.json_out,
        md_out=args.md_out,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
