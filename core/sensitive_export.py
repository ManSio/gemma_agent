"""Redact sensitive fields before audit JSON is written or printed (CodeQL storage/logging guard)."""
from __future__ import annotations

from typing import Any, Dict, List

_MEM0_ERROR_CODES = frozenset(
    {
        None,
        "no_key",
        "invalid_key",
        "http_error",
        "timeout",
        "network_error",
        "mem0_skipped",
    }
)


def mem0_check_public_view(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Drop tainted Mem0 connectivity fields; keep operator-safe scalars only."""
    code = raw.get("error_code")
    safe_code = code if code in _MEM0_ERROR_CODES else ("error" if code else None)
    return {
        "ok": bool(raw.get("ok")),
        "skipped": bool(raw.get("skipped")),
        "http_status": raw.get("http_status"),
        "error_code": safe_code,
        "roundtrip_ms": raw.get("roundtrip_ms"),
    }


def scan_finding_public(row: Dict[str, Any]) -> Dict[str, Any]:
    leaks = row.get("leaks") or []
    return {
        "file": row.get("file"),
        "index": row.get("index"),
        "role": row.get("role"),
        "text_len": row.get("text_len"),
        "leak_codes": row.get("leak_codes") or [str(lk.get("code")) for lk in leaks if isinstance(lk, dict)],
    }


def scan_report_public(rep: Dict[str, Any]) -> Dict[str, Any]:
    findings = rep.get("findings") or []
    return {
        "ts": rep.get("ts"),
        "archive_dir": rep.get("archive_dir"),
        "scan_dirs": rep.get("scan_dirs"),
        "files_scanned": rep.get("files_scanned"),
        "messages_scanned": rep.get("messages_scanned"),
        "findings_count": rep.get("findings_count"),
        "by_code": rep.get("by_code"),
        "findings": [scan_finding_public(f) for f in findings if isinstance(f, dict)],
    }
