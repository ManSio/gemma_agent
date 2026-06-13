#!/usr/bin/env python3
"""Полный аудит сервера: turns, llm_usage, errors, архивы, сравнение качества."""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _parse_row_ts(raw: Any) -> Optional[datetime]:
    """Parse ts/timestamp from JSONL row to UTC datetime."""
    if raw is None:
        return None
    try:
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(float(raw), tz=timezone.utc)
        s = str(raw).strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError, OSError):
        return None


def _load_jsonl(
    path: Path,
    *,
    days: int = 14,
    day: str = "",
) -> List[Dict[str, Any]]:
    """Load JSONL rows filtered by rolling window or single UTC calendar day."""
    if not path.is_file():
        return []
    cut = datetime.now(timezone.utc) - timedelta(days=days)
    day_key = str(day or "").strip()[:10]
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        dt = _parse_row_ts(row.get("ts") or row.get("timestamp"))
        if day_key:
            if dt is None or dt.strftime("%Y-%m-%d") != day_key:
                continue
        elif dt is not None and dt < cut:
            continue
        rows.append(row)
    return rows


def _pct(n: int, d: int) -> str:
    if d <= 0:
        return "—"
    return f"{100.0 * n / d:.1f}%"


def _p50(vals: List[float]) -> Optional[float]:
    if not vals:
        return None
    return float(statistics.median(vals))


def _suspect_incomplete(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 120:
        return False
    last = t[-1]
    if last in ".!?…。！？」»\"')":
        return False
    if t.endswith("...") or t.endswith("…"):
        return False
    return last.isalnum()


def audit_host(
    root: Path,
    *,
    host_label: str,
    days: int = 14,
    day: str = "",
) -> Dict[str, Any]:
    from scripts.scan_archive_leaks import scan_archives

    turns_path = root / "data/runtime/turns.jsonl"
    llm_path = Path(os.getenv("GEMMA_LLM_USAGE_PATH") or root / "data/llm_usage.jsonl")
    err_path = root / "data/runtime_errors.jsonl"
    day_key = str(day or "").strip()[:10]

    turns = [
        t
        for t in _load_jsonl(turns_path, days=days, day=day_key)
        if t.get("type") != "scenario"
    ]
    latencies = [
        float(t["latency_ms"])
        for t in turns
        if isinstance(t.get("latency_ms"), (int, float)) and t["latency_ms"] > 0
    ]
    issues = Counter(i for t in turns for i in (t.get("issues") or []))
    outcomes = Counter(t.get("outcome") for t in turns)
    profiles = Counter(t.get("profile") for t in turns)

    incomplete = [
        t
        for t in turns
        if _suspect_incomplete(str(t.get("assistant_excerpt") or ""))
        and len(str(t.get("user_excerpt") or "")) > 80
    ]
    long_short = [
        t
        for t in turns
        if len(str(t.get("user_excerpt") or "")) > 200
        and len(str(t.get("assistant_excerpt") or "")) < 150
    ]

    llm_rows = _load_jsonl(llm_path, days=days, day=day_key)
    brain_lat = [
        float(r["latency_ms"])
        for r in llm_rows
        if isinstance(r.get("latency_ms"), (int, float))
        and r["latency_ms"] > 0
        and "brain" in str(r.get("telemetry_tag") or r.get("tag") or "")
    ]
    recent_lim = Counter(
        str(r.get("brain_recent_limit") or "(none)")
        for r in llm_rows
        if "brain" in str(r.get("telemetry_tag") or r.get("tag") or "")
    )

    errs = _load_jsonl(err_path, days=days, day=day_key)
    err_top = Counter(
        f"{e.get('component','')}:{e.get('kind') or 'error'}"
        for e in errs
    )

    arch = scan_archives(root)
    try:
        from core.data_paths import behavior_dir, message_archive_dir

        arch_dirs = {
            "behavior_dir": str(behavior_dir()),
            "message_archive_dir": str(message_archive_dir()),
        }
    except Exception:
        arch_dirs = {}
    git_head = ""
    try:
        git_head = (
            subprocess.check_output(["git", "-C", str(root), "log", "-1", "--oneline"], text=True)
            .strip()
        )
    except Exception:
        pass

    return {
        "host": host_label,
        "root": str(root),
        "git_head": git_head,
        "window_days": 1 if day_key else days,
        "window_day": day_key or None,
        "turns": {
            "count": len(turns),
            "outcomes": dict(outcomes),
            "issues_top": issues.most_common(15),
            "profiles_top": profiles.most_common(10),
            "latency_ms_p50": _p50(latencies),
            "latency_ms_p90": (
                sorted(latencies)[int(len(latencies) * 0.9) - 1] if len(latencies) >= 10 else None
            ),
            "latency_samples": len(latencies),
            "suspect_incomplete_excerpt": len(incomplete),
            "long_q_short_a": len(long_short),
            "with_brain_recent_limit": sum(
                1 for t in turns if int(t.get("brain_recent_limit") or 0) > 0
            ),
            "with_prompt_tokens_est": sum(
                1 for t in turns if int(t.get("prompt_tokens_est") or 0) > 0
            ),
            "samples_incomplete": [
                {
                    "ts": t.get("ts"),
                    "profile": t.get("profile"),
                    "latency_ms": t.get("latency_ms"),
                    "user_len": len(str(t.get("user_excerpt") or "")),
                    "assistant_len": len(str(t.get("assistant_excerpt") or "")),
                }
                for t in incomplete[:8]
            ],
            "samples_long_short": [
                {
                    "ts": t.get("ts"),
                    "user_len": len(str(t.get("user_excerpt") or "")),
                    "assistant_len": len(str(t.get("assistant_excerpt") or "")),
                }
                for t in long_short[:5]
            ],
        },
        "llm_usage": {
            "path": str(llm_path),
            "rows": len(llm_rows),
            "brain_latency_p50_ms": _p50(brain_lat),
            "brain_recent_limit_top": recent_lim.most_common(8),
        },
        "errors": {"count": len(errs), "top": err_top.most_common(12)},
        "archives": {
            "files": arch.get("files_scanned"),
            "messages": arch.get("messages_scanned"),
            "leaks": arch.get("findings_count"),
            "by_code": arch.get("by_code"),
            **arch_dirs,
        },
    }


def compare(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    ta, tb = a["turns"], b["turns"]
    return {
        "latency_p50_delta_ms": (ta.get("latency_ms_p50") or 0) - (tb.get("latency_ms_p50") or 0),
        "turns_count_ratio": ta["count"] / max(1, tb["count"]),
        "incomplete_ratio_a": _pct(ta["suspect_incomplete_excerpt"], ta["count"]),
        "incomplete_ratio_b": _pct(tb["suspect_incomplete_excerpt"], tb["count"]),
        "issues_a": ta.get("issues_top", [])[:8],
        "issues_b": tb.get("issues_top", [])[:8],
    }


def render_md(report: Dict[str, Any]) -> str:
    lines = [
        f"# Server audit {report.get('ts', '')}",
        "",
    ]
    for host in report.get("hosts", []):
        if host.get("error"):
            lines += [
                f"## {host.get('host', '?')}",
                "",
                f"- **error:** {host.get('error')}",
                "",
            ]
            continue
        t = host.get("turns") or {}
        lines += [
            f"## {host['host']} (`{host.get('git_head', '')}`)",
            "",
            f"- turns (14d): **{t['count']}**",
            f"- latency p50: **{t.get('latency_ms_p50')}** ms, p90: **{t.get('latency_ms_p90')}** ms",
            f"- outcomes: {t.get('outcomes')}",
            f"- issues: {t.get('issues_top')}",
            f"- incomplete (excerpt heuristic): **{t['suspect_incomplete_excerpt']}** ({_pct(t['suspect_incomplete_excerpt'], t['count'])})",
            f"- long Q / short A: **{t['long_q_short_a']}**",
            f"- turns with brain_recent_limit: **{t['with_brain_recent_limit']}**",
            f"- archive leaks: **{host['archives'].get('leaks')}** / msgs {host['archives'].get('messages')}",
            f"- errors (14d): **{host['errors']['count']}** top {host['errors'].get('top', [])[:5]}",
            "",
        ]
        if t.get("samples_incomplete"):
            lines.append("### Подозрение на обрыв (excerpt ≤480 символов — часть ложных)")
            for s in t["samples_incomplete"][:4]:
                lines.append(
                    f"- `{s.get('ts')}` {s.get('profile')}: "
                    f"user_len={s.get('user_len')} assistant_len={s.get('assistant_len')}"
                )
            lines.append("")
    cmp_ = report.get("compare")
    if cmp_:
        lines += [
            "## Сравнение deploy-host vs VPS",
            "",
            f"- Δ latency p50: **{cmp_.get('latency_p50_delta_ms'):.0f}** ms (deploy-host − VPS)",
            f"- incomplete excerpt: deploy-host {cmp_.get('incomplete_ratio_a')}, VPS {cmp_.get('incomplete_ratio_b')}",
            "",
        ]
    fixes = report.get("fixes_applied") or []
    if fixes:
        lines += ["## Исправления в этом коммите", ""] + [f"- {x}" for x in fixes] + [""]
    root_causes = report.get("root_causes") or []
    if root_causes:
        lines += ["## Корневые причины неполных ответов", ""] + [f"- {x}" for x in root_causes] + [""]
    return "\n".join(lines)


def _load_project_env(root: Path) -> None:
    """Подтянуть .env и PROJECT_ROOT до data_paths / scan_archives."""
    os.environ.setdefault("PROJECT_ROOT", str(root))
    env_path = root / ".env"
    if env_path.is_file():
        try:
            from dotenv import load_dotenv

            load_dotenv(env_path, override=False)
        except ImportError:
            for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, _, v = s.partition("=")
                k = k.strip()
                if k and k not in os.environ:
                    os.environ[k] = v.strip().strip('"').strip("'")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(_ROOT))
    ap.add_argument("--host-label", default="local")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument(
        "--date",
        default="",
        help="UTC calendar day YYYY-MM-DD (overrides rolling --days window)",
    )
    ap.add_argument("--json-out", default="")
    ap.add_argument("--md-out", default="")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    _load_project_env(root)
    rep = audit_host(
        root,
        host_label=args.host_label,
        days=args.days,
        day=str(args.date or "").strip(),
    )
    rep["ts"] = datetime.now(timezone.utc).isoformat()
    out_doc = {"ts": rep["ts"], "hosts": [rep]}
    from core.sensitive_export import (
        audit_document_public,
        audit_summary_log_line,
        write_audit_document_json,
        write_audit_document_md,
    )

    safe_doc = audit_document_public(out_doc)
    stamp_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    epoch = int(time.time())
    if args.json_out:
        p = Path(args.json_out)
        if not p.is_absolute():
            p = root / p
        write_audit_document_json(
            p,
            out_doc,
            host_labels=(args.host_label,),
            stamp_day=stamp_day,
            exported_at_epoch=epoch,
        )
        print(f"Wrote {p}")
    else:
        print(audit_summary_log_line(len(safe_doc.get("hosts") or [])))
    if args.md_out:
        mp = Path(args.md_out)
        if not mp.is_absolute():
            mp = root / mp
        write_audit_document_md(
            mp,
            out_doc,
            host_labels=(args.host_label,),
            stamp_day=stamp_day,
            exported_at_epoch=epoch,
        )
        print(f"Wrote {mp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
