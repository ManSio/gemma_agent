"""Форматирование /admin_reputation: маршруты (module+intent) и скиллы."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

_REPORT_ROUTE_L = 28
_REPORT_SKILL_L = 24


def _f(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _i(val: Any, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _parse_route_key(key: str, uid: str) -> Optional[Tuple[str, str]]:
    """user|module|intent → (module, intent)."""
    k = (key or "").strip()
    if not k.startswith(f"{uid}|"):
        return None
    parts = k.split("|", 2)
    if len(parts) != 3:
        return None
    return parts[1], parts[2]


def _parse_skill_key(key: str, uid: str) -> Optional[str]:
    """user|skill → skill_name."""
    k = (key or "").strip()
    prefix = f"{uid}|"
    if not k.startswith(prefix):
        return None
    rest = k[len(prefix) :]
    if not rest or "|" in rest:
        return None
    return rest


def _entry_from_value(val: Any) -> Dict[str, Any]:
    if not isinstance(val, dict):
        return {}
    return {
        "v_c": round(_f(val.get("v_c"), 0.5), 3),
        "v_p": round(_f(val.get("v_p"), 0.5), 3),
        "confidence": round(_f(val.get("v_c"), 0.5), 3),
        "n_ok": _i(val.get("n_ok")),
        "n_bad": _i(val.get("n_bad")),
        "fail_streak": _i(val.get("fail_streak")),
        "updated_ts": val.get("updated_ts"),
    }


def _sort_key(entry: Dict[str, Any]) -> Tuple[float, int, float]:
    """Полезность: выше v_c, больше ok, ниже v_p."""
    vc = _f(entry.get("v_c"))
    vp = _f(entry.get("v_p"))
    n_ok = _i(entry.get("n_ok"))
    return (vc - vp, n_ok, vc)


def build_admin_reputation_payload(
    user_id: str,
    route_rows: Dict[str, Any],
    skill_rows: Optional[Dict[str, Any]] = None,
    *,
    branch: str = "main",
) -> Dict[str, Any]:
    uid = str(user_id or "").strip()
    routes: List[Dict[str, Any]] = []
    for key, val in (route_rows or {}).items():
        parsed = _parse_route_key(str(key), uid)
        if not parsed:
            continue
        mod, intent = parsed
        ent = _entry_from_value(val)
        ent["key"] = key
        ent["module"] = mod
        ent["intent"] = intent
        routes.append(ent)
    routes.sort(key=_sort_key, reverse=True)

    skills: List[Dict[str, Any]] = []
    for key, val in (skill_rows or {}).items():
        skill = _parse_skill_key(str(key), uid)
        if not skill:
            continue
        ent = _entry_from_value(val)
        ent["key"] = key
        ent["skill"] = skill
        skills.append(ent)
    skills.sort(key=_sort_key, reverse=True)

    return {
        "user_id": uid,
        "branch": branch,
        "reputation_routes": routes,
        "reputation_skills": skills,
        "reputation_rows": route_rows,
        "skill_reputation_rows": skill_rows or {},
        "summary": {
            "routes_count": len(routes),
            "skills_count": len(skills),
            "top_skills": [s["skill"] for s in skills[:8] if _f(s.get("v_c")) >= 0.55],
            "weak_skills": [s["skill"] for s in skills if _f(s.get("v_c")) < 0.4 and _i(s.get("n_bad")) >= 2],
        },
    }


def load_admin_reputation_payload(
    user_id: str,
    *,
    branch: Optional[str] = None,
) -> Dict[str, Any]:
    """Загрузить reputation + reputation_skill из agent KV."""
    from core.agent_kv.store import agent_kv_branch, agent_kv_enabled, iter_prefix

    if not agent_kv_enabled():
        return {"error": "AGENT_KV_ENABLED=false", "user_id": str(user_id or "").strip()}
    uid = str(user_id or "").strip()
    if not uid:
        return {"error": "empty user_id"}
    br = (branch or "").strip() or agent_kv_branch()
    prefix = f"{uid}|"
    rows = {k: v for k, v in iter_prefix("reputation", prefix, branch=br)}
    skill_rows = {k: v for k, v in iter_prefix("reputation_skill", prefix, branch=br)}
    return build_admin_reputation_payload(uid, rows, skill_rows, branch=br)


def _confidence_badge(v_c: float) -> str:
    if v_c >= 0.7:
        return "🟢"
    if v_c >= 0.45:
        return "🟡"
    return "🔴"


def _route_rows_pre(routes: List[Dict[str, Any]], *, limit: int = 14) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for r in routes[:limit]:
        mod = str(r.get("module") or "?")
        intent = str(r.get("intent") or "?")
        vc = _f(r.get("v_c"))
        vp = _f(r.get("v_p"))
        label = f"{_confidence_badge(vc)} {mod}·{intent}"[: _REPORT_ROUTE_L]
        val = f"v_c={vc:.2f} v_p={vp:.2f} ok={_i(r.get('n_ok'))} bad={_i(r.get('n_bad'))}"
        out.append((label, val))
    return out


def _skill_rows_pre(skills: List[Dict[str, Any]], *, limit: int = 14) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for s in skills[:limit]:
        name = str(s.get("skill") or "?")
        vc = _f(s.get("v_c"))
        vp = _f(s.get("v_p"))
        label = f"{_confidence_badge(vc)} {name}"[:_REPORT_SKILL_L]
        val = f"v_c={vc:.2f} v_p={vp:.2f} ok={_i(s.get('n_ok'))} bad={_i(s.get('n_bad'))}"
        out.append((label, val))
    return out


def format_admin_reputation_html(payload: Dict[str, Any], *, route_limit: int = 14, skill_limit: int = 14) -> str:
    """HTML-отчёт для /admin_reputation (стиль как /admin_llm_usage)."""
    from core.telegram_ui import esc, report_pre_kv

    if payload.get("error"):
        return sanitize_html_reputation_error(str(payload.get("error")))

    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    routes = payload.get("reputation_routes") if isinstance(payload.get("reputation_routes"), list) else []
    skills = payload.get("reputation_skills") if isinstance(payload.get("reputation_skills"), list) else []

    lines = [
        "⭐ <b>Репутация · CDC</b>",
        "",
        "<blockquote><i>v_c — кооперативная уверенность, v_p — штрафной поток. "
        "Маршруты: module+intent; скиллы: имя skill из brain.</i></blockquote>",
        "",
        "👤 <b>Пользователь</b>",
        "",
        "<blockquote>",
        report_pre_kv(
            [
                ("user_id", str(payload.get("user_id") or "?")),
                ("branch", str(payload.get("branch") or "main")),
                ("маршрутов", str(summary.get("routes_count", len(routes)))),
                ("скиллов", str(summary.get("skills_count", len(skills)))),
            ],
            label_max=14,
        ),
        "</blockquote>",
        "",
    ]

    top_sk = summary.get("top_skills") or []
    weak_sk = summary.get("weak_skills") or []
    if top_sk or weak_sk:
        hint_rows: List[Tuple[str, str]] = []
        if top_sk:
            hint_rows.append(("Сильные скиллы", ", ".join(str(x) for x in top_sk[:6])))
        if weak_sk:
            hint_rows.append(("Слабые скиллы", ", ".join(str(x) for x in weak_sk[:6])))
        lines.extend(["📌 <b>Сводка скиллов</b>", "", "<blockquote>", report_pre_kv(hint_rows, label_max=16), "</blockquote>", ""])

    if routes:
        lines.extend(
            [
                "🛤 <b>Маршруты</b> <i>(module · intent)</i>",
                "",
                "<blockquote>",
                report_pre_kv(_route_rows_pre(routes, limit=route_limit), label_max=_REPORT_ROUTE_L, value_max=36),
                "</blockquote>",
                "",
            ]
        )
    else:
        lines.extend(["🛤 <b>Маршруты</b>", "", "<blockquote><i>Нет записей reputation для этого user_id.</i></blockquote>", ""])

    if skills:
        lines.extend(
            [
                "🎯 <b>Скиллы</b>",
                "",
                "<blockquote>",
                report_pre_kv(_skill_rows_pre(skills, limit=skill_limit), label_max=_REPORT_SKILL_L, value_max=36),
                "</blockquote>",
                "",
            ]
        )
    elif not routes:
        lines.append("<blockquote><i>Нет записей reputation_skill.</i></blockquote>")
        lines.append("")

    lines.append(
        "<blockquote><i>JSON: <code>/admin_reputation_json</code>"
        f" · сброс: <code>/admin_reputation_reset</code> &lt;key&gt;</i></blockquote>"
    )
    return "\n".join(lines)


def sanitize_html_reputation_error(msg: str) -> str:
    from core.telegram_ui import esc

    return f"⭐ <b>Репутация</b>\n\n<blockquote>⚠️ {esc(msg)}</blockquote>"
