"""Redact sensitive fields before audit JSON is written or printed (CodeQL storage/logging guard)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


def mem0_log_facets(raw: Dict[str, Any]) -> Tuple[bool, Optional[int], Optional[str]]:
    """Scalars safe for logging — no raw API bodies or key material."""
    pub = mem0_check_public_view(raw if isinstance(raw, dict) else {})
    ok = bool(pub.get("ok"))
    http_status = pub.get("http_status")
    code = pub.get("error_code")
    error_code = str(code) if code in _MEM0_ERROR_CODES and code else None
    return ok, http_status, error_code


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
    return {
        "host": str(host.get("host") or "local")[:64],
        "git_head": str(host.get("git_head") or "")[:120],
        "window_days": int(host.get("window_days") or 0),
        "error_type": str(host.get("error_type") or "")[:64] if host.get("error_type") else None,
        "turns": {
            "count": int(turns.get("count") or 0),
            "outcomes": dict(turns.get("outcomes") or {}),
            "issues_top": list(turns.get("issues_top") or [])[:15],
            "profiles_top": list(turns.get("profiles_top") or [])[:10],
            "latency_ms_p50": turns.get("latency_ms_p50"),
            "latency_ms_p90": turns.get("latency_ms_p90"),
            "latency_samples": int(turns.get("latency_samples") or 0),
            "suspect_incomplete_excerpt": int(turns.get("suspect_incomplete_excerpt") or 0),
            "long_q_short_a": int(turns.get("long_q_short_a") or 0),
            "with_brain_recent_limit": int(turns.get("with_brain_recent_limit") or 0),
            "with_prompt_tokens_est": int(turns.get("with_prompt_tokens_est") or 0),
            "samples_incomplete": list(turns.get("samples_incomplete") or [])[:8],
            "samples_long_short": list(turns.get("samples_long_short") or [])[:5],
        },
        "llm_usage": {
            "rows": int(llm.get("rows") or 0),
            "brain_latency_p50_ms": llm.get("brain_latency_p50_ms"),
            "brain_recent_limit_top": list(llm.get("brain_recent_limit_top") or [])[:8],
        },
        "errors": {
            "count": int(errors.get("count") or 0),
            "top": [(str(k)[:120], int(v)) for k, v in err_top[:12]],
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
