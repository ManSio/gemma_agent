"""HTML-отчёт /admin_route_risk_clusters."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple


def format_route_risk_clusters_html(pack: Dict[str, Any], *, limit: int = 12) -> str:
    from core.telegram_ui import esc, report_pre_kv

    clusters = pack.get("clusters") if isinstance(pack.get("clusters"), list) else []
    hours = pack.get("window_hours", "?")
    total = int(pack.get("total_stumbles") or 0)
    gen = str(pack.get("generated_at") or "")[:19]

    lines = [
        "⚠️ <b>Кластеры route_risk</b>",
        "",
        "<blockquote><i>Повторяющиеся сбои маршрута за окно (fingerprint + intent + module). "
        "Без ML — группировка по ключу кластера.</i></blockquote>",
        "",
        "<blockquote>",
        report_pre_kv(
            [
                ("окно", f"{hours} ч"),
                ("stumbles", str(total)),
                ("кластеров", str(len(clusters))),
                ("сгенер.", gen or "—"),
            ],
            label_max=12,
        ),
        "</blockquote>",
        "",
    ]

    if not clusters:
        lines.append("<blockquote><i>Нет кластеров с min_count≥2 за окно.</i></blockquote>")
    else:
        rows: List[Tuple[str, str]] = []
        for c in clusters[:limit]:
            if not isinstance(c, dict):
                continue
            cnt = int(c.get("count") or 0)
            et = str(c.get("error_type") or "?")[:14]
            intent = str(c.get("intent") or "?")[:12]
            mod = str(c.get("module") or "?")[:10]
            badge = "🔴" if cnt >= 5 else "🟡" if cnt >= 3 else "🟢"
            label = f"{badge} {et}·{intent}"[:28]
            val = f"×{cnt} {mod} · {(str(c.get('sample_detail') or '')[:40])}"
            rows.append((label, val))
        lines.extend(
            [
                "📊 <b>Топ кластеров</b>",
                "",
                "<blockquote>",
                report_pre_kv(rows, label_max=28, value_max=42),
                "</blockquote>",
            ]
        )

    lines.append(
        "<blockquote><i>JSON: <code>/admin_route_risk_clusters_json</code>"
        " · часы: <code>/admin_route_risk_clusters 12</code></i></blockquote>"
    )
    return "\n".join(lines)
