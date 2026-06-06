from core.admin_kv_state_view import format_grim_state_html, format_self_model_html, format_session_task_html
from core.admin_mce_status_view import format_mce_status_html
from core.admin_route_risk_view import format_route_risk_clusters_html
from core.memory_runtime_report import format_memory_insight_html


def test_format_route_risk_clusters_html():
    pack = {
        "window_hours": 6,
        "total_stumbles": 10,
        "clusters": [
            {
                "count": 4,
                "error_type": "fallback",
                "intent": "math",
                "module": "chat",
                "sample_detail": "timeout",
            }
        ],
        "generated_at": "2026-05-18T12:00:00+00:00",
    }
    html = format_route_risk_clusters_html(pack)
    assert "⚠️" in html
    assert "fallback" in html
    assert "/admin_route_risk_clusters_json" in html
    assert "<pre>" in html


def test_format_grim_and_self_model_html():
    grim_html = format_grim_state_html(
        {"user_id": "u1", "branch": "main", "grim": {"v_c": 0.7}, "cdc_policy": {"tier": 2}}
    )
    assert "grim" in grim_html.lower() or "Grim" in grim_html
    sm_html = format_self_model_html({"user_id": "u1", "self_model": {"confidence": 0.8, "role": "assistant"}})
    assert "confidence" in sm_html
    st_html = format_session_task_html(
        {"user_id": "u1", "session_task": {"intent": "math", "module": "chat", "outcome": "ok"}}
    )
    assert "math" in st_html


def test_format_mce_status_html():
    html = format_mce_status_html(
        {
            "enabled": True,
            "tick_counter": 3,
            "tick_interval": 5,
            "experiment_enabled": False,
            "recommendations_pending": 1,
            "history_total": 10,
            "self_state": {"confidence": 0.55, "confidence_trend": "up", "safe_mode": False},
        }
    )
    assert "MCE" in html
    assert "/admin_mce_status_json" in html


def test_format_memory_insight_html_minimal():
    html = format_memory_insight_html(
        {
            "generated_at": "2026-05-18T10:00:00+00:00",
            "limits": {"entries_per_file": 5},
            "flags": {"STRATEGY_PATH_MEMORY_ENABLED": True},
            "strategy_paths_tail": [],
            "route_risk_tail": [],
            "experience_tail": [],
        }
    )
    assert "Память" in html
    assert "/admin_memory_insight_json" in html
