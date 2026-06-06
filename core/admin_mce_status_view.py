"""HTML для /admin_mce_status."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Tuple


def format_mce_status_html(snap: Dict[str, Any]) -> str:
    from core.telegram_ui import esc, report_pre_kv

    ss = snap.get("self_state") if isinstance(snap.get("self_state"), dict) else {}
    exp = snap.get("active_experiment") if isinstance(snap.get("active_experiment"), dict) else None

    meta_rows: List[Tuple[str, str]] = [
        ("включён", "да" if snap.get("enabled") else "нет"),
        ("тиков", str(snap.get("tick_counter", 0))),
        ("интервал", f"каждый {snap.get('tick_interval', 5)}-й tick"),
        ("эксперименты", "вкл" if snap.get("experiment_enabled") else "выкл"),
        ("рекомендаций", str(snap.get("recommendations_pending", 0))),
        ("история MCE", str(snap.get("history_total", 0))),
        ("auto_apply", "да" if snap.get("auto_apply") else "нет"),
    ]

    lines = [
        "🎯 <b>Meta-Cognitive Engine</b>",
        "",
        "<blockquote>",
        report_pre_kv(meta_rows, label_max=14),
        "</blockquote>",
        "",
    ]

    if ss:
        conf = float(ss.get("confidence", 0.5) or 0.5)
        badge = "🟢" if conf >= 0.6 else "🟡" if conf >= 0.4 else "🔴"
        ss_rows: List[Tuple[str, str]] = [
            (f"{badge} confidence", f"{conf:.2f} ({ss.get('confidence_trend', '?')})"),
            ("safe mode", "да" if ss.get("safe_mode") else "нет"),
            ("lessons", f"{ss.get('lesson_active_count', 0)} (eff {float(ss.get('lesson_avg_effectiveness', 0) or 0):.2f})"),
            ("exp hit-rate", f"{float(ss.get('experience_hit_rate_100', 0) or 0):.0%}"),
            ("route risk", str(ss.get("route_risk_active", 0))),
            ("healer 24h", str(ss.get("healer_actions_24h", 0))),
            ("p95 telegram", f"{float(ss.get('p95_telegram_ms', 0) or 0):.0f} ms"),
            ("p95 openrouter", f"{float(ss.get('p95_openrouter_ms', 0) or 0):.0f} ms"),
        ]
        disabled = ss.get("healer_disabled_modules") or []
        if disabled:
            ss_rows.append(("disabled mods", ", ".join(str(x) for x in disabled[:6])))
        lines.extend(["<b>Self-State</b>", "", "<blockquote>", report_pre_kv(ss_rows, label_max=16), "</blockquote>", ""])
    else:
        lines.extend(["<b>Self-State</b>", "", "<blockquote><i>ещё нет синтеза</i></blockquote>", ""])

    thresholds = snap.get("dynamic_thresholds") if isinstance(snap.get("dynamic_thresholds"), dict) else {}
    if thresholds:
        th_rows = [(str(k), f"{float(v):.2f}") for k, v in list(thresholds.items())[:8]]
        lines.extend(["<b>Пороги</b>", "", "<blockquote>", report_pre_kv(th_rows, label_max=14), "</blockquote>", ""])

    if exp:
        lines.append(
            f"<blockquote>🧪 Эксперимент: <code>{esc(str(exp.get('param', '?')))}</code> "
            f"{esc(str(exp.get('control_value', '?')))} → {esc(str(exp.get('treatment_value', '?')))} "
            f"({esc(str(exp.get('status', '?')))})</blockquote>"
        )
        lines.append("")
    else:
        lines.append("<blockquote><i>Эксперимент не активен</i></blockquote>")
        lines.append("")

    hist = snap.get("history_recent") if isinstance(snap.get("history_recent"), list) else []
    if hist:
        lines.append("<b>Последние события</b>")
        lines.append("<blockquote>")
        for h in hist[:4]:
            if isinstance(h, dict):
                ht = time.strftime("%H:%M", time.localtime(float(h.get("ts", 0) or 0)))
                he = esc(str(h.get("event_type", "?")))
                lines.append(f"• <code>{ht}</code> {he}")
        lines.append("</blockquote>")
        lines.append("")

    goals = snap.get("goals") if isinstance(snap.get("goals"), list) else []
    if goals:
        g_rows: List[Tuple[str, str]] = []
        for g in goals[:5]:
            if isinstance(g, dict):
                g_rows.append(
                    (
                        str(g.get("status", "?"))[:8],
                        f"{float(g.get('progress_pct', 0) or 0):.0f}% — {str(g.get('description', '?'))[:50]}",
                    )
                )
        lines.extend(["<b>Цели</b>", "", "<blockquote>", report_pre_kv(g_rows, label_max=10), "</blockquote>", ""])

    ld = float(snap.get("last_digest_ts", 0) or 0)
    if ld:
        lines.append(f"<blockquote>Последний digest: {time.strftime('%m-%d %H:%M', time.localtime(ld))}</blockquote>")
        lines.append("")

    lines.append("<blockquote><i>JSON: <code>/admin_mce_status_json</code></i></blockquote>")
    return "\n".join(lines)
