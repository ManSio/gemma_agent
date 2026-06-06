from core.sensitive_export import (
    audit_document_public,
    audit_host_public,
    mem0_check_public_view,
    mem0_log_facets,
    scan_report_public,
    security_audit_public_report,
)


def test_mem0_check_public_view_strips_tainted_fields():
    raw = {
        "ok": False,
        "user_message": "secret-ish body",
        "error_code": "invalid_key",
        "http_status": 401,
    }
    pub = mem0_check_public_view(raw)
    assert "user_message" not in pub
    assert pub["error_code"] == "invalid_key"
    assert pub["http_status"] == 401


def test_mem0_log_facets_safe_scalars():
    ok, status, code = mem0_log_facets(
        {"ok": False, "error_code": "invalid_key", "http_status": 401, "user_message": "x"}
    )
    assert ok is False
    assert status == 401
    assert code == "invalid_key"


def test_scan_report_public_strips_snippets():
    rep = {
        "findings_count": 1,
        "findings": [
            {
                "file": "/secret/path/user_archive.json",
                "index": 0,
                "role": "user",
                "text_len": 99,
                "leak_codes": ["secret_like"],
                "leaks": [{"code": "secret_like", "snippet": "sk-or-v1-SECRET"}],
            }
        ],
    }
    pub = scan_report_public(rep)
    row = pub["findings"][0]
    assert "leaks" not in row
    assert row["file"] == "user_archive.json"
    assert row["leak_codes"] == ["secret_like"]


def test_audit_host_public_drops_root_path():
    host = audit_host_public(
        {
            "host": "vps",
            "root": "/srv/gemma_bot",
            "turns": {"count": 3},
            "archives": {"files": 1, "messages": 2, "leaks": 0},
        }
    )
    assert "root" not in host
    assert host["host"] == "vps"


def test_security_audit_public_report_no_root_path():
    pub = security_audit_public_report(
        {
            "root": "/opt/gemma_agent",
            "passed": True,
            "failed_checks": [],
            "checks": {"secrets_configured": {"ok": True, "notes": ["ok"]}},
        }
    )
    assert "root" not in pub
    assert pub["product"] == "gemma_agent"


def test_audit_document_public_roundtrip():
    doc = audit_document_public(
        {
            "ts": "2026-06-06T12:00:00Z",
            "hosts": [{"host": "local", "root": "/x", "turns": {"count": 1}}],
        }
    )
    assert doc["hosts"][0]["host"] == "local"
    assert "root" not in doc["hosts"][0]
