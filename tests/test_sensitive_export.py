from core.sensitive_export import mem0_check_public_view, scan_report_public


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


def test_scan_report_public_strips_snippets():
    rep = {
        "findings_count": 1,
        "findings": [
            {
                "file": "a.json",
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
    assert row["leak_codes"] == ["secret_like"]
