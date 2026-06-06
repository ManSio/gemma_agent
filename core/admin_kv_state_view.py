"""HTML для admin_grim_state, admin_self_model, admin_session_task, admin_kv_branches."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple


def _preview_dict(d: Dict[str, Any], keys: List[str], *, max_len: int = 80) -> List[Tuple[str, str]]:
    rows: List[Tuple[str, str]] = []
    for k in keys:
        if k not in d:
            continue
        v = d.get(k)
        if isinstance(v, (dict, list)):
            s = json.dumps(v, ensure_ascii=False)[:max_len]
        else:
            s = str(v)[:max_len]
        rows.append((k, s + ("…" if len(str(v)) > max_len else "")))
    return rows


def format_grim_state_html(payload: Dict[str, Any]) -> str:
    from core.telegram_ui import esc, report_pre_kv

    if payload.get("error"):
        return f"🩺 <b>Grim / CDC</b>\n\n<blockquote>⚠️ {esc(str(payload['error']))}</blockquote>"

    grim = payload.get("grim") if isinstance(payload.get("grim"), dict) else {}
    cdc = payload.get("cdc_policy") if isinstance(payload.get("cdc_policy"), dict) else {}
    lines = [
        "🩺 <b>Grim · CDC policy</b>",
        "",
        "<blockquote>",
        report_pre_kv(
            [
                ("user_id", str(payload.get("user_id") or "?")),
                ("branch", str(payload.get("branch") or "main")),
            ],
            label_max=10,
        ),
        "</blockquote>",
        "",
    ]
    grim_keys = ["v_c", "v_p", "n_ok", "n_bad", "fail_streak", "last_intent", "last_module", "updated_ts"]
    grim_rows = _preview_dict(grim, [k for k in grim_keys if k in grim] or list(grim.keys())[:10])
    if grim_rows:
        lines.extend(["<b>grim</b>", "", "<blockquote>", report_pre_kv(grim_rows, label_max=14), "</blockquote>", ""])
    else:
        lines.extend(["<b>grim</b>", "", "<blockquote><i>(пусто)</i></blockquote>", ""])

    cdc_keys = ["coop", "penalty", "tier", "safe_mode", "last_route"]
    cdc_rows = _preview_dict(cdc, [k for k in cdc_keys if k in cdc] or list(cdc.keys())[:12])
    if cdc_rows:
        lines.extend(["<b>cdc_policy</b>", "", "<blockquote>", report_pre_kv(cdc_rows, label_max=14), "</blockquote>", ""])
    else:
        lines.extend(["<b>cdc_policy</b>", "", "<blockquote><i>(пусто)</i></blockquote>", ""])

    lines.append("<blockquote><i>JSON: <code>/admin_grim_state_json</code> [user_id]</i></blockquote>")
    return "\n".join(lines)


def format_self_model_html(payload: Dict[str, Any]) -> str:
    from core.telegram_ui import esc, report_pre_kv

    sm = payload.get("self_model") if isinstance(payload.get("self_model"), dict) else {}
    lines = [
        "🪞 <b>Само-модель агента</b>",
        "",
        "<blockquote>",
        report_pre_kv(
            [
                ("user_id", str(payload.get("user_id") or "?")),
                ("branch", str(payload.get("branch") or "—")),
                ("ключей", str(len(sm))),
            ],
            label_max=10,
        ),
        "</blockquote>",
        "",
    ]
    if not sm:
        lines.append("<blockquote><i>Нет self_model в KV и behavior.</i></blockquote>")
    else:
        priority = [
            "confidence",
            "role",
            "capabilities",
            "limits",
            "last_updated",
            "persona",
            "goals",
        ]
        rows = _preview_dict(sm, [k for k in priority if k in sm] or list(sm.keys())[:14], max_len=120)
        lines.extend(["<blockquote>", report_pre_kv(rows, label_max=14, value_max=48), "</blockquote>"])
    lines.append("")
    lines.append("<blockquote><i>JSON: <code>/admin_self_model_json</code> [user_id]</i></blockquote>")
    return "\n".join(lines)


def format_session_task_html(payload: Dict[str, Any]) -> str:
    from core.telegram_ui import report_pre_kv

    st = payload.get("session_task") if isinstance(payload.get("session_task"), dict) else {}
    lines = [
        "🧭 <b>Последний ход сессии</b>",
        "",
        "<blockquote>",
        report_pre_kv(
            [
                ("user_id", str(payload.get("user_id") or "?")),
                ("group_id", str(payload.get("group_id") or "—")),
            ],
            label_max=10,
        ),
        "</blockquote>",
        "",
    ]
    if not st:
        lines.append("<blockquote><i>session_task пуст (ещё не было хода или нет behavior).</i></blockquote>")
    else:
        route_keys = ["intent", "module", "outcome", "skill", "task_tier", "ts", "user_excerpt"]
        tool = st.get("last_tool") if isinstance(st.get("last_tool"), dict) else {}
        rows = _preview_dict(st, [k for k in route_keys if k in st], max_len=100)
        if tool:
            rows.append(("tool", str(tool.get("name") or tool.get("tool") or "?")[:40]))
            det = str(tool.get("detail") or tool.get("args_preview") or "")[:60]
            if det:
                rows.append(("tool_detail", det))
        lines.extend(["<blockquote>", report_pre_kv(rows, label_max=14, value_max=52), "</blockquote>"])
    lines.append("")
    lines.append("<blockquote><i>JSON: <code>/admin_session_task_json</code> [user_id] [group_id]</i></blockquote>")
    return "\n".join(lines)


def format_kv_branches_html(payload: Dict[str, Any]) -> str:
    from core.telegram_ui import esc

    branches = payload.get("branches") if isinstance(payload.get("branches"), list) else []
    lines = ["🌿 <b>KV ветки</b>", ""]
    if not branches:
        lines.append("<blockquote><i>(нет веток)</i></blockquote>")
    else:
        items = ", ".join(f"<code>{esc(str(b))}</code>" for b in branches[:24])
        lines.append(f"<blockquote>{items}</blockquote>")
    lines.append("")
    lines.append("<blockquote><i>JSON: <code>/admin_kv_branches_json</code></i></blockquote>")
    return "\n".join(lines)


def load_grim_state_payload(user_id: str, *, branch: Optional[str] = None) -> Dict[str, Any]:
    from core.agent_kv.store import agent_kv_branch, agent_kv_enabled, get_json

    if not agent_kv_enabled():
        return {"error": "AGENT_KV_ENABLED=false"}
    uid = str(user_id or "").strip()
    br = (branch or "").strip() or agent_kv_branch()
    return {
        "user_id": uid,
        "branch": br,
        "grim": get_json("grim", uid, branch=br) or {},
        "cdc_policy": get_json("cdc_policy", uid, branch=br) or {},
    }
