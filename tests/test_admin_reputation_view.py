from core.admin_reputation_view import build_admin_reputation_payload, format_admin_reputation_html
from core.cdc.engine import _skill_agg_key, process_turn_end


def test_build_admin_reputation_payload_routes_and_skills():
    uid = "u99"
    route_rows = {
        f"{uid}|chat|general": {"v_c": 0.7, "v_p": 0.2, "n_ok": 5, "n_bad": 1},
        f"{uid}|math|reasoning": {"v_c": 0.3, "v_p": 0.8, "n_ok": 1, "n_bad": 4},
    }
    skill_rows = {
        f"{uid}|weather_skill": {"v_c": 0.85, "v_p": 0.1, "n_ok": 10, "n_bad": 0},
        f"{uid}|bad_skill": {"v_c": 0.25, "v_p": 0.9, "n_ok": 0, "n_bad": 5},
    }
    payload = build_admin_reputation_payload(uid, route_rows, skill_rows, branch="main")
    assert payload["summary"]["routes_count"] == 2
    assert payload["summary"]["skills_count"] == 2
    assert payload["reputation_skills"][0]["skill"] == "weather_skill"
    assert payload["reputation_skills"][0]["confidence"] == 0.85
    assert "weather_skill" in payload["summary"]["top_skills"]
    assert "bad_skill" in payload["summary"]["weak_skills"]


def test_format_admin_reputation_html_contains_tables():
    uid = "u99"
    payload = build_admin_reputation_payload(
        uid,
        {f"{uid}|math|general": {"v_c": 0.8, "v_p": 0.1, "n_ok": 3, "n_bad": 0}},
        {f"{uid}|translator": {"v_c": 0.9, "v_p": 0.05, "n_ok": 5, "n_bad": 0}},
        branch="main",
    )
    html = format_admin_reputation_html(payload)
    assert "⭐" in html
    assert "math" in html
    assert "translator" in html
    assert "/admin_reputation_json" in html
    assert "<pre>" in html


def test_process_turn_end_skill_mirror(tmp_path, monkeypatch):
    import json
    import os

    agg_p = tmp_path / "cdc_agg.json"
    log_p = tmp_path / "cdc_log.jsonl"
    monkeypatch.setenv("CDC_ENGINE_ENABLED", "true")
    monkeypatch.setenv("GEMMA_CDC_AGGREGATES_PATH", str(agg_p))
    monkeypatch.setenv("GEMMA_CDC_TURN_LOG", str(log_p))
    monkeypatch.setenv("AGENT_KV_ENABLED", "false")
    uid = "sk_user"
    process_turn_end(
        user_id=uid,
        user_text="погода",
        intent="general",
        module="chat",
        outcome="ok",
        skill_name="weather_skill",
    )
    assert os.path.isfile(agg_p)
    blob = json.loads(agg_p.read_text(encoding="utf-8"))
    sk = _skill_agg_key(uid, "weather_skill")
    assert sk in blob
    assert blob[sk]["n_ok"] == 1
    assert float(blob[sk]["v_c"]) > 0.5
