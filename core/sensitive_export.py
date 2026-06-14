"""Redact sensitive fields before audit JSON is written or printed (CodeQL storage/logging guard)."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

SECURITY_AUDIT_CHECK_KEYS: Tuple[str, ...] = (
    "env_not_tracked",
    "dotenv_permissions",
    "privacy_scan",
    "secrets_configured",
    "security_layer_tests",
    "release_guard_smoke",
)

_MEM0_ERROR_CODES = frozenset(
    {
        None,
        "no_key",
        "invalid_key",
        "http_error",
        "timeout",
        "network_error",
        "mem0_skipped",
        "error",
    }
)


def _safe_int(value: Any, *, lo: int, hi: int) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and lo <= value <= hi:
        return value
    if isinstance(value, float) and value == int(value) and lo <= int(value) <= hi:
        return int(value)
    if isinstance(value, str) and value.isdigit():
        v = int(value)
        if lo <= v <= hi:
            return v
    return None


def mem0_check_public_view(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Drop tainted Mem0 connectivity fields; keep operator-safe scalars only."""
    code = raw.get("error_code")
    safe_code = code if code in _MEM0_ERROR_CODES else ("error" if code else None)
    return {
        "ok": bool(raw.get("ok")),
        "skipped": bool(raw.get("skipped")),
        "http_status": _safe_int(raw.get("http_status"), lo=100, hi=599),
        "error_code": safe_code,
        "roundtrip_ms": _safe_int(raw.get("roundtrip_ms"), lo=0, hi=600_000),
    }


def mem0_path_log_facets(path: str) -> Tuple[str, int]:
    """Mem0 API path as operator-safe kind + length (no raw path in logs)."""
    p_low = str(path or "").lower()
    if "/memories/add" in p_low:
        kind = "memories_add"
    elif "/memories/search" in p_low:
        kind = "memories_search"
    elif "/memories/delete" in p_low:
        kind = "memories_delete"
    else:
        kind = "other"
    return kind, len(str(path or ""))


def mem0_log_facets(raw: Dict[str, Any]) -> Tuple[bool, Optional[int], Optional[str]]:
    """Scalars safe for logging — no raw API bodies or key material."""
    pub = mem0_check_public_view(raw if isinstance(raw, dict) else {})
    ok = bool(pub.get("ok"))
    http_status = pub.get("http_status")
    code = pub.get("error_code")
    error_code = str(code) if code in _MEM0_ERROR_CODES and code else None
    return ok, http_status, error_code


def hash_sensitive_text(value: Any, *, max_len: int = 64) -> Optional[str]:
    """One-way hash for audit logs (user_id, topic snippets)."""
    s = str(value or "").strip()
    if not s:
        return None
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:max_len]


def build_heuristic_miss_row(
    *,
    rule_id: str,
    verdict: str,
    reason: str,
    user_text: str,
    topic_current: str = "",
    user_id: str = "",
    ts: str,
) -> Dict[str, Any]:
    """Audit-safe heuristic_misses.jsonl row — no raw user text or ids."""
    return {
        "ts": ts,
        "rule_id": str(rule_id or "")[:64],
        "verdict": str(verdict or "")[:32],
        "reason": str(reason or "")[:120],
        "text_len": len((user_text or "").strip()),
        "text_excerpt_redacted": True,
        "topic_current_hash": hash_sensitive_text(topic_current),
        "user_id_hash": hash_sensitive_text(user_id),
    }


def _safe_label_pairs(raw: Any, *, limit: int = 15) -> List[List[Union[str, int]]]:
    """Truncate (label, count) pairs for public audit export."""
    out: List[List[Union[str, int]]] = []
    for item in (raw or [])[:limit]:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            out.append([str(item[0])[:64], int(item[1])])
        elif isinstance(item, str):
            out.append([str(item)[:64], 1])
    return out


def _count_nonneg(value: Any) -> int:
    """Non-negative int for CodeQL-safe export payloads."""
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def audit_host_counts_row(host: Dict[str, Any]) -> Dict[str, int]:
    """Per-host ops counters only (no string fields from runtime logs)."""
    if not isinstance(host, dict):
        return {}
    turns = host.get("turns") if isinstance(host.get("turns"), dict) else {}
    archives = host.get("archives") if isinstance(host.get("archives"), dict) else {}
    errors = host.get("errors") if isinstance(host.get("errors"), dict) else {}
    return {
        "turns_count": _count_nonneg(turns.get("count")),
        "incomplete_count": _count_nonneg(turns.get("suspect_incomplete_excerpt")),
        "long_q_short_a": _count_nonneg(turns.get("long_q_short_a")),
        "brain_recent_limit_turns": _count_nonneg(turns.get("with_brain_recent_limit")),
        "archive_leaks": _count_nonneg(archives.get("findings_count") or archives.get("leaks")),
        "archive_messages": _count_nonneg(
            archives.get("messages_scanned") or archives.get("messages")
        ),
        "errors_count": _count_nonneg(errors.get("count")),
    }


def audit_document_counts_payload(
    doc: Dict[str, Any],
    *,
    host_labels: Sequence[str] = (),
    stamp_day: str = "",
    exported_at_epoch: int = 0,
) -> Dict[str, Any]:
    """Audit JSON body with ints/bools only — breaks CodeQL string taint to disk."""
    hosts_in = doc.get("hosts") if isinstance(doc.get("hosts"), list) else []
    rows = [audit_host_counts_row(h) for h in hosts_in if isinstance(h, dict)]
    payload: Dict[str, Any] = {
        "exported_at_epoch": max(0, int(exported_at_epoch)),
        "hosts_count": len(rows),
        "hosts": rows,
    }
    if stamp_day:
        payload["stamp_day"] = str(stamp_day)[:10]
    if host_labels:
        payload["host_labels"] = [str(x)[:64] for x in host_labels[:32]]
    return payload


def scan_counts_payload(raw: Dict[str, Any]) -> Dict[str, int]:
    """Archive scan counters only (no paths or leak snippets)."""
    rep = raw if isinstance(raw, dict) else {}
    return {
        "files_scanned": _count_nonneg(rep.get("files_scanned")),
        "messages_scanned": _count_nonneg(rep.get("messages_scanned")),
        "findings_count": _count_nonneg(rep.get("findings_count")),
    }


def write_audit_document_json(
    path: Union[str, Path],
    doc: Dict[str, Any],
    *,
    host_labels: Sequence[str] = (),
    stamp_day: str = "",
    exported_at_epoch: int = 0,
) -> None:
    """Write ops audit JSON (counts only, operator host labels)."""
    payload = audit_document_counts_payload(
        doc if isinstance(doc, dict) else {},
        host_labels=host_labels,
        stamp_day=stamp_day,
        exported_at_epoch=exported_at_epoch,
    )
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_scan_report_json(path: Union[str, Path], raw: Dict[str, Any]) -> None:
    """Write archive leak scan JSON (counts only)."""
    payload = scan_counts_payload(raw if isinstance(raw, dict) else {})
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def security_audit_stdout_json(report: Dict[str, Any]) -> str:
    """Security audit JSON for stdout (literal check keys, bool values only)."""
    pub = security_audit_public_report(report if isinstance(report, dict) else {})
    checks = {
        name: {
            "ok": bool((pub.get("checks") or {}).get(name, {}).get("ok")),
            "skipped": bool((pub.get("checks") or {}).get(name, {}).get("skipped")),
        }
        for name in SECURITY_AUDIT_CHECK_KEYS
    }
    return json.dumps(
        {"product": "gemma_agent", "passed": bool(pub.get("passed")), "checks": checks},
        ensure_ascii=False,
        indent=2,
    )


def security_audit_public_json_text(report: Dict[str, Any]) -> str:
    """Full sanitized security audit JSON (for file export, not stdout)."""
    pub = security_audit_public_report(report if isinstance(report, dict) else {})
    return json.dumps(pub, ensure_ascii=False, indent=2)


def audit_summary_log_line(host_count: int) -> str:
    """One-line audit summary safe for stdout (counts only)."""
    n = max(0, int(host_count))
    return f"AUDIT hosts={n}"


def scan_summary_log_line(*, files: int, messages: int, leaks: int) -> str:
    """One-line archive scan summary safe for stdout (counts only)."""
    return (
        f"SUMMARY files={max(0, int(files))} msgs={max(0, int(messages))} "
        f"leaks={max(0, int(leaks))}"
    )


def render_audit_counts_md(
    *,
    hosts: List[Dict[str, int]],
    host_labels: Sequence[str] = (),
    stamp_day: str = "",
) -> str:
    """Ops markdown from counters and operator host labels only."""
    title = f"# Ops digest ({stamp_day})" if stamp_day else "# Server audit"
    lines = [title, ""]
    for idx, row in enumerate(hosts):
        label = host_labels[idx] if idx < len(host_labels) else f"host_{idx + 1}"
        lines += [
            f"## {label}",
            "",
            f"- turns: **{row.get('turns_count', 0)}**",
            f"- incomplete (excerpt heuristic): **{row.get('incomplete_count', 0)}**",
            f"- long Q / short A: **{row.get('long_q_short_a', 0)}**",
            f"- turns with brain_recent_limit: **{row.get('brain_recent_limit_turns', 0)}**",
            f"- archive leaks: **{row.get('archive_leaks', 0)}** / msgs {row.get('archive_messages', 0)}",
            f"- errors: **{row.get('errors_count', 0)}**",
            "",
        ]
    return "\n".join(lines)


def render_daily_ops_md(
    *,
    hosts: List[Dict[str, Any]],
    host_labels: Sequence[str] = (),
    stamp_day: str = "",
    backfill_note: str = "",
) -> str:
    """Daily ops markdown: counts + latency/outcomes (no user excerpts)."""
    title = f"# Ops digest ({stamp_day})" if stamp_day else "# Ops digest"
    lines = [title, ""]
    if backfill_note:
        lines += [f"> {backfill_note}", ""]
    for idx, host in enumerate(hosts):
        if not isinstance(host, dict):
            continue
        label = host_labels[idx] if idx < len(host_labels) else str(host.get("host") or f"host_{idx + 1}")
        row = audit_host_counts_row(host)
        turns = host.get("turns") if isinstance(host.get("turns"), dict) else {}
        llm = host.get("llm_usage") if isinstance(host.get("llm_usage"), dict) else {}
        errors = host.get("errors") if isinstance(host.get("errors"), dict) else {}
        lines += [f"## {label}", ""]
        gh = str(host.get("git_head") or "").strip()
        if gh:
            lines.append(f"- git (snapshot): `{gh[:96]}`")
        lines.append(f"- turns: **{row.get('turns_count', 0)}**")
        p50 = turns.get("latency_ms_p50")
        p90 = turns.get("latency_ms_p90")
        if p50 is not None or p90 is not None:
            lines.append(f"- latency p50: **{p50 if p50 is not None else '—'}** ms · p90: **{p90 if p90 is not None else '—'}** ms")
        if llm.get("rows") is not None:
            lines.append(f"- llm_usage rows: **{int(llm.get('rows') or 0)}**")
        lines += [
            f"- incomplete (excerpt heuristic): **{row.get('incomplete_count', 0)}**",
            f"- long Q / short A: **{row.get('long_q_short_a', 0)}**",
            f"- turns with brain_recent_limit: **{row.get('brain_recent_limit_turns', 0)}**",
            f"- archive leaks: **{row.get('archive_leaks', 0)}** / msgs {row.get('archive_messages', 0)}",
            f"- errors: **{row.get('errors_count', 0)}**",
        ]
        outcomes = turns.get("outcomes") if isinstance(turns.get("outcomes"), dict) else {}
        if outcomes:
            parts = [f"{k}={v}" for k, v in sorted(outcomes.items(), key=lambda x: (-int(x[1] or 0), str(x[0])))[:8]]
            lines.append(f"- outcomes: {', '.join(parts)}")
        err_top = errors.get("top") if isinstance(errors.get("top"), list) else []
        if err_top:
            et = ", ".join(f"{a}:{b}" for a, b in err_top[:5] if a)
            lines.append(f"- errors top: {et}")
        note = str(host.get("note") or "").strip()
        if note:
            lines.append(f"- note: {note}")
        elif int(row.get("turns_count") or 0) == 0:
            lines.append("- note: нет пользовательских ходов на VPS за этот UTC-день")
        lines.append("")
    return "\n".join(lines)


def render_audit_document_md(doc: Dict[str, Any]) -> str:
    """Operator-safe markdown for audit/digest (counts only, no excerpts)."""
    payload = audit_document_counts_payload(doc if isinstance(doc, dict) else {})
    return render_audit_counts_md(
        hosts=payload.get("hosts") or [],
        host_labels=payload.get("host_labels") or (),
        stamp_day=str(payload.get("stamp_day") or ""),
    )


def write_audit_counts_md_from_json(
    md_path: Union[str, Path],
    json_path: Union[str, Path],
) -> None:
    """Render counts-only ops markdown from a prior JSON export file."""
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        data = {}
    md = render_audit_counts_md(
        hosts=data.get("hosts") if isinstance(data.get("hosts"), list) else [],
        host_labels=data.get("host_labels") if isinstance(data.get("host_labels"), list) else [],
        stamp_day=str(data.get("stamp_day") or "")[:32],
    )
    out = Path(md_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")


def scan_finding_public(row: Dict[str, Any]) -> Dict[str, Any]:
    leaks = row.get("leaks") or []
    file_name = Path(str(row.get("file") or "")).name[:160]
    return {
        "file": file_name,
        "index": _safe_int(row.get("index"), lo=0, hi=10_000_000),
        "role": str(row.get("role") or "")[:32],
        "text_len": _safe_int(row.get("text_len"), lo=0, hi=10_000_000),
        "leak_codes": row.get("leak_codes")
        or [str(lk.get("code"))[:64] for lk in leaks if isinstance(lk, dict)],
    }


def scan_report_public(rep: Dict[str, Any]) -> Dict[str, Any]:
    findings = rep.get("findings") or []
    by_code = rep.get("by_code") if isinstance(rep.get("by_code"), dict) else {}
    return {
        "ts": str(rep.get("ts") or "")[:64],
        "archive_dir": Path(str(rep.get("archive_dir") or "")).name[:160],
        "scan_dirs": [Path(str(p)).name[:160] for p in (rep.get("scan_dirs") or [])[:32]],
        "files_scanned": int(rep.get("files_scanned") or 0),
        "messages_scanned": int(rep.get("messages_scanned") or 0),
        "findings_count": int(rep.get("findings_count") or 0),
        "by_code": {str(k)[:64]: int(v) for k, v in by_code.items()},
        "findings": [scan_finding_public(f) for f in findings if isinstance(f, dict)],
    }


def audit_host_public(host: Dict[str, Any]) -> Dict[str, Any]:
    """Server audit row without paths, excerpts, or archive snippets."""
    if not isinstance(host, dict):
        return {}
    turns = host.get("turns") if isinstance(host.get("turns"), dict) else {}
    llm = host.get("llm_usage") if isinstance(host.get("llm_usage"), dict) else {}
    archives = host.get("archives") if isinstance(host.get("archives"), dict) else {}
    errors = host.get("errors") if isinstance(host.get("errors"), dict) else {}

    arch_pub: Dict[str, Any]
    if archives.get("findings") is not None:
        arch_pub = scan_report_public(archives)
    else:
        arch_pub = {
            "files_scanned": int(archives.get("files") or archives.get("files_scanned") or 0),
            "messages_scanned": int(archives.get("messages") or archives.get("messages_scanned") or 0),
            "findings_count": int(archives.get("leaks") or archives.get("findings_count") or 0),
            "by_code": archives.get("by_code") if isinstance(archives.get("by_code"), dict) else {},
        }

    err_top = errors.get("top") if isinstance(errors.get("top"), list) else []
    issues_top = turns.get("issues_top") if isinstance(turns.get("issues_top"), list) else []
    profiles_top = turns.get("profiles_top") if isinstance(turns.get("profiles_top"), list) else []
    return {
        "host": str(host.get("host") or "local")[:64],
        "git_head": str(host.get("git_head") or "")[:120],
        "window_days": int(host.get("window_days") or 0),
        "error_type": str(host.get("error_type") or "")[:64] if host.get("error_type") else None,
        "turns": {
            "count": int(turns.get("count") or 0),
            "outcomes": {
                str(k)[:64]: int(v)
                for k, v in (turns.get("outcomes") or {}).items()
                if k is not None
            },
            "issues_count": len(issues_top),
            "issues_top": _safe_label_pairs(issues_top, limit=15),
            "profiles_top": _safe_label_pairs(profiles_top, limit=10),
            "latency_ms_p50": turns.get("latency_ms_p50"),
            "latency_ms_p90": turns.get("latency_ms_p90"),
            "latency_samples": int(turns.get("latency_samples") or 0),
            "suspect_incomplete_excerpt": int(turns.get("suspect_incomplete_excerpt") or 0),
            "long_q_short_a": int(turns.get("long_q_short_a") or 0),
            "with_brain_recent_limit": int(turns.get("with_brain_recent_limit") or 0),
            "with_prompt_tokens_est": int(turns.get("with_prompt_tokens_est") or 0),
        },
        "llm_usage": {
            "rows": int(llm.get("rows") or 0),
            "brain_latency_p50_ms": llm.get("brain_latency_p50_ms"),
            "brain_recent_limit_top": _safe_label_pairs(llm.get("brain_recent_limit_top"), limit=8),
        },
        "errors": {
            "count": int(errors.get("count") or 0),
            "kinds_count": len(err_top),
        },
        "archives": arch_pub,
    }


def audit_document_public(doc: Dict[str, Any]) -> Dict[str, Any]:
    hosts_in = doc.get("hosts") if isinstance(doc.get("hosts"), list) else []
    return {
        "ts": str(doc.get("ts") or "")[:64],
        "stamp": str(doc.get("stamp") or "")[:32],
        "hosts": [audit_host_public(h) for h in hosts_in if isinstance(h, dict)],
    }


def security_audit_public_report(report: Dict[str, Any]) -> Dict[str, Any]:
    """CLI/CI security audit JSON without .env paths or subprocess blobs."""
    checks_out: Dict[str, Any] = {}
    for name, data in (report.get("checks") or {}).items():
        if not isinstance(data, dict):
            continue
        notes = [str(n)[:280] for n in (data.get("notes") or [])[:24]]
        checks_out[str(name)] = {
            "ok": bool(data.get("ok")),
            "skipped": bool(data.get("skipped")),
            "notes": notes,
            "detail_line_count": len(data.get("detail") or []),
        }
    return {
        "product": "gemma_agent",
        "passed": bool(report.get("passed")),
        "failed_checks": [str(x) for x in (report.get("failed_checks") or [])],
        "checks": checks_out,
    }


def _dialogue_rows_redacted(rows: Any, *, tail: int = 8) -> List[Dict[str, Any]]:
    """Dialogue slice for disk: role + length + hash only."""
    if not isinstance(rows, list):
        return []
    out: List[Dict[str, Any]] = []
    for r in rows[-max(1, tail) :]:
        if not isinstance(r, dict):
            continue
        text = str(r.get("text") or r.get("content") or "")
        out.append(
            {
                "role": str(r.get("role") or "")[:16],
                "text_len": len(text.strip()),
                "text_hash": hash_sensitive_text(text),
            }
        )
    return out


def sanitize_ops_trace_row_for_disk(row: Dict[str, Any]) -> Dict[str, Any]:
    """Ops trace JSONL row without clear-text user content (CodeQL + private VPS)."""
    if not isinstance(row, dict):
        return {}
    ut = str(row.get("user_text") or "")
    at = str(row.get("assistant_text") or "")
    uid = str(row.get("user_id") or "")
    issues = row.get("issues") if isinstance(row.get("issues"), list) else []
    plan = row.get("plan_steps") if isinstance(row.get("plan_steps"), list) else []
    reasoning = row.get("reasoning") if isinstance(row.get("reasoning"), dict) else {}
    extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
    safe_extra: Dict[str, Any] = {}
    for k, v in extra.items():
        if k in {"profile", "outcome", "brain_profile", "router_profile", "lane"}:
            safe_extra[str(k)[:48]] = str(v)[:120]
    return {
        "ts": str(row.get("ts") or "")[:64],
        "type": str(row.get("type") or "turn")[:32],
        "user_id_hash": hash_sensitive_text(uid),
        "group_id_present": bool(row.get("group_id")),
        "channel": str(row.get("channel") or "")[:64],
        "trace_id": str(row.get("trace_id") or "")[:64],
        "user_text_len": len(ut.strip()),
        "assistant_text_len": len(at.strip()),
        "user_text_hash": hash_sensitive_text(ut),
        "assistant_text_hash": hash_sensitive_text(at),
        "recent_before": _dialogue_rows_redacted(row.get("recent_before")),
        "recent_after": _dialogue_rows_redacted(row.get("recent_after")),
        "archive_tail_in_prompt": _dialogue_rows_redacted(row.get("archive_tail_in_prompt"), tail=10),
        "plan_steps": [str(x)[:64] for x in plan[:24]],
        "reasoning_keys": [str(k)[:48] for k in reasoning.keys()][:16],
        "latency_ms": _safe_int(row.get("latency_ms"), lo=0, hi=600_000),
        "issues": [str(x)[:64] for x in issues[:16]],
        "ok": bool(row.get("ok")),
        "extra": safe_extra,
    }


def autolearn_log_facets(
    *,
    user_id: str = "",
    lesson_id: str = "",
    pending_id: str = "",
    fingerprint: str = "",
    distinct_users: int = 0,
) -> Dict[str, Any]:
    """Scalars safe for ephemeral_autolearn logs (no raw user id)."""
    return {
        "user_id_hash": hash_sensitive_text(user_id),
        "lesson_id_head": str(lesson_id or "")[:24],
        "pending_id_head": str(pending_id or "")[:24],
        "fingerprint_head": str(fingerprint or "")[:16],
        "distinct_users": max(0, int(distinct_users or 0)),
    }


_LLM_USAGE_PERSIST_KEYS = frozenset(
    {
        "ts",
        "ok",
        "requested_model",
        "upstream_model",
        "latency_ms",
        "http_status",
        "content_chars",
        "error",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cost",
        "cached_prompt_tokens",
        "cache_write_tokens",
        "kind",
        "tag",
        "telemetry_tag",
        "telemetry_kind",
        "type",
        "llm_model",
        "self_verify_run",
        "self_verify_result",
        "consistency_checked",
        "consistency_ok",
        "consistency_conflicts_count",
        "consistency_recommendation",
        "fetch_methods_used",
        "total_sources",
        "avg_confidence",
        "trusted_domain_count",
        "brain_profile",
        "finish_reason",
    }
)


def llm_usage_row_for_disk(row: Dict[str, Any]) -> Dict[str, Any]:
    """Whitelist LLM usage JSONL row — no user query/reply taint to disk."""
    if not isinstance(row, dict):
        return {}
    if str(row.get("type") or "") == "news_generation":
        return {
            "type": "news_generation",
            "timestamp": str(row.get("timestamp") or row.get("ts") or "")[:64],
            "llm_model": str(row.get("llm_model") or "")[:120],
            "self_verify_run": bool(row.get("self_verify_run", False)),
            "self_verify_result": str(row.get("self_verify_result") or "N/A")[:80],
            "consistency_checked": bool(row.get("consistency_checked", False)),
            "consistency_ok": bool(row.get("consistency_ok", True)),
            "consistency_conflicts_count": _count_nonneg(row.get("consistency_conflicts_count")),
            "consistency_recommendation": str(row.get("consistency_recommendation") or "safe")[:80],
            "fetch_methods_used": [str(x)[:48] for x in list(row.get("fetch_methods_used") or [])[:20]],
            "total_sources": _count_nonneg(row.get("total_sources")),
            "avg_confidence": float(row.get("avg_confidence") or 0.0),
            "trusted_domain_count": _count_nonneg(row.get("trusted_domain_count")),
        }
    out: Dict[str, Any] = {}
    for key in _LLM_USAGE_PERSIST_KEYS:
        if key not in row:
            continue
        val = row[key]
        if key == "error":
            out[key] = str(val or "")[:400]
        elif key in {"fetch_methods_used"}:
            out[key] = [str(x)[:48] for x in list(val or [])[:20]]
        else:
            out[key] = val
    src = row.get("sources")
    if isinstance(src, list):
        out["sources"] = [
            {
                "fetch_method": str(item.get("fetch_method") or "unknown")[:48],
                "fetch_success": bool(item.get("fetch_success", True)),
                "text_length": _count_nonneg(item.get("text_length")),
                "parsing_confidence": float(item.get("parsing_confidence") or 0.0),
            }
            for item in src[:32]
            if isinstance(item, dict)
        ]
    return out


def write_ops_trace_jsonl(path: Union[str, Path], row: Dict[str, Any]) -> None:
    """Append one redacted ops_trace JSONL row (CodeQL clear-text-storage barrier)."""
    safe_row = sanitize_ops_trace_row_for_disk(row if isinstance(row, dict) else {})
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(safe_row, ensure_ascii=False, default=str) + "\n")
        f.flush()


def write_llm_usage_jsonl(path: Union[str, Path], row: Dict[str, Any]) -> None:
    """Append one whitelist LLM usage JSONL row (CodeQL clear-text-storage barrier)."""
    safe_row = llm_usage_row_for_disk(row if isinstance(row, dict) else {})
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(safe_row, ensure_ascii=False, default=str) + "\n"
    with open(p, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


def log_ephemeral_pending_auto_promoted(
    *,
    pending_id: str = "",
    lesson_id: str = "",
    distinct_users: int = 0,
    logger_name: str = "core.ephemeral_autolearn",
) -> None:
    """Log pending auto-promote without raw user ids (CodeQL clear-text-logging barrier)."""
    facets = autolearn_log_facets(
        lesson_id=lesson_id,
        pending_id=pending_id,
        distinct_users=distinct_users,
    )
    logging.getLogger(logger_name).info(
        "ephemeral pending auto-promoted (new) pending=%s lesson=%s distinct_users=%d",
        facets["pending_id_head"],
        facets["lesson_id_head"],
        facets["distinct_users"],
    )


def log_autolearn_lesson_promoted(
    *,
    user_id: str = "",
    lesson_id: str = "",
    fingerprint: str = "",
    logger_name: str = "core.ephemeral_autolearn",
) -> None:
    """Log lesson promotion with hashed user id only (CodeQL clear-text-logging barrier)."""
    facets = autolearn_log_facets(
        user_id=user_id,
        lesson_id=lesson_id,
        fingerprint=fingerprint,
    )
    logging.getLogger(logger_name).info(
        "ephemeral_autolearn promoted lesson=%s user_hash=%s fp=%s",
        facets["lesson_id_head"],
        facets["user_id_hash"],
        facets["fingerprint_head"],
    )
