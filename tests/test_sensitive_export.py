import json

from core.sensitive_export import (
    SECURITY_AUDIT_CHECK_KEYS,
    audit_document_counts_payload,
    audit_document_public,
    audit_host_counts_row,
    audit_host_public,
    audit_summary_log_line,
    build_heuristic_miss_row,
    mem0_check_public_view,
    mem0_log_facets,
    mem0_path_log_facets,
    render_audit_counts_md,
    scan_counts_payload,
    scan_report_public,
    scan_summary_log_line,
    security_audit_public_json_text,
    security_audit_public_report,
    security_audit_stdout_json,
    write_audit_document_json,
    write_audit_document_md,
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


def test_build_heuristic_miss_row_no_raw_text():
    row = build_heuristic_miss_row(
        rule_id="geo_nearby",
        verdict="blocked",
        reason="prose_over_chars",
        user_text="secret user phrase",
        topic_current="topic secret",
        user_id="12345",
        ts="2026-06-13T00:00:00Z",
    )
    assert row["text_excerpt_redacted"] is True
    assert "secret" not in json.dumps(row)
    assert row["user_id_hash"]
    assert row["topic_current_hash"]


def test_mem0_path_log_facets_kinds():
    kind, n = mem0_path_log_facets("/v1/memories/add/")
    assert kind == "memories_add"
    assert n > 0
    kind2, _ = mem0_path_log_facets("/other")
    assert kind2 == "other"


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


def test_scan_counts_payload_ints_only():
    counts = scan_counts_payload(
        {"files_scanned": 3, "messages_scanned": 9, "findings_count": 1, "findings": [{"x": 1}]}
    )
    assert counts == {"files_scanned": 3, "messages_scanned": 9, "findings_count": 1}


def test_audit_host_public_drops_root_path_and_samples():
    host = audit_host_public(
        {
            "host": "vps",
            "root": "/srv/gemma_bot",
            "turns": {
                "count": 3,
                "samples_incomplete": [{"user_len": 99, "assistant_len": 120}],
                "issues_top": [("empty_response", 2)],
            },
            "archives": {"files": 1, "messages": 2, "leaks": 0},
        }
    )
    assert "root" not in host
    assert host["host"] == "vps"
    assert "samples_incomplete" not in host["turns"]
    assert host["turns"]["issues_count"] == 1
    assert host["errors"]["kinds_count"] == 0


def test_audit_host_counts_row_ints_only():
    row = audit_host_counts_row(
        {
            "turns": {"count": 5, "suspect_incomplete_excerpt": 1},
            "archives": {"leaks": 0, "messages": 10},
            "errors": {"count": 2},
        }
    )
    assert row["turns_count"] == 5
    assert row["errors_count"] == 2
    assert all(isinstance(v, int) for v in row.values())


def test_audit_document_counts_payload():
    payload = audit_document_counts_payload(
        {"hosts": [{"turns": {"count": 2}}]},
        host_labels=("local",),
        stamp_day="2026-06-13",
        exported_at_epoch=1,
    )
    assert payload["hosts_count"] == 1
    assert payload["host_labels"] == ["local"]
    assert payload["hosts"][0]["turns_count"] == 2


def test_audit_summary_log_line_counts_only():
    assert audit_summary_log_line(2) == "AUDIT hosts=2"


def test_scan_summary_log_line_counts_only():
    line = scan_summary_log_line(files=10, messages=20, leaks=1)
    assert line == "SUMMARY files=10 msgs=20 leaks=1"
    assert "by_code" not in line


def test_render_audit_counts_md_uses_labels():
    md = render_audit_counts_md(
        hosts=[{"turns_count": 5, "incomplete_count": 1, "long_q_short_a": 0,
                "brain_recent_limit_turns": 0, "archive_leaks": 0, "archive_messages": 10,
                "errors_count": 0}],
        host_labels=("local",),
        stamp_day="2026-06-13",
    )
    assert "Ops digest" in md
    assert "## local" in md
    assert "samples" not in md


def test_write_audit_document_json_counts_only(tmp_path):
    doc = {"hosts": [{"turns": {"count": 1}, "archives": {}, "errors": {}}]}
    out = tmp_path / "audit.json"
    write_audit_document_json(out, doc, host_labels=("local",), exported_at_epoch=100)
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["hosts"][0]["turns_count"] == 1
    assert loaded["host_labels"] == ["local"]
    assert "root" not in json.dumps(loaded)


def test_write_audit_document_md(tmp_path):
    doc = {
        "hosts": [{"turns": {"count": 1}, "archives": {}, "errors": {"count": 0}}],
    }
    out = tmp_path / "digest.md"
    write_audit_document_md(out, doc, host_labels=("local",), stamp_day="2026-06-13")
    text = out.read_text(encoding="utf-8")
    assert "Ops digest" in text
    assert "## local" in text


def test_security_audit_stdout_json_literal_keys():
    text = security_audit_stdout_json(
        {
            "root": "/opt/gemma_agent",
            "passed": True,
            "failed_checks": [],
            "checks": {"secrets_configured": {"ok": True, "notes": ["secret path"]}},
        }
    )
    assert "/opt/gemma_agent" not in text
    assert "secret path" not in text
    loaded = json.loads(text)
    assert set(loaded["checks"]) == set(SECURITY_AUDIT_CHECK_KEYS)


def test_security_audit_public_json_text_no_root():
    text = security_audit_public_json_text(
        {
            "root": "/opt/gemma_agent",
            "passed": True,
            "failed_checks": [],
            "checks": {"secrets_configured": {"ok": True, "notes": ["ok"]}},
        }
    )
    assert "/opt/gemma_agent" not in text
    assert "secrets_configured" in text


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
