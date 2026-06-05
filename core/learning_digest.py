"""Сводка самообучения: уроки, опыт, репутация скиллов, route_risk — без LLM."""
from __future__ import annotations

import json
import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _runtime_dir() -> Path:
    root = (os.getenv("GEMMA_PROJECT_ROOT") or ".").strip() or "."
    return Path(root) / "data" / "runtime"


def _tail_jsonl(path: Path, limit: int = 200) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: List[Dict[str, Any]] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                out.append(row)
        except json.JSONDecodeError:
            continue
    return out


def _lesson_stats() -> Dict[str, Any]:
    try:
        from core.self_learning.lesson_manager import LessonManager

        lm = LessonManager.get_instance()
        active = lm.load_active_lessons()
        effs = [float(x.effectiveness_score) for x in active if x.effectiveness_score is not None]
        return {
            "active_lessons": len(active),
            "avg_effectiveness": round(sum(effs) / len(effs), 3) if effs else None,
            "low_eff_count": sum(1 for e in effs if e < 0.35),
        }
    except Exception as e:
        return {"error": str(e)[:120]}


def _experience_stats(hours: float = 24.0) -> Dict[str, Any]:
    path = _runtime_dir() / "experience_digest.jsonl"
    cutoff = time.time() - hours * 3600
    rows = _tail_jsonl(path, 500)
    ok = bad = 0
    by_intent: Counter[str] = Counter()
    by_skill: Counter[str] = Counter()
    for r in rows:
        ts = r.get("ts")
        try:
            if isinstance(ts, str):
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                t = dt.timestamp()
            else:
                t = float(ts or 0)
        except Exception:
            t = 0
        if t and t < cutoff:
            continue
        oc = str(r.get("outcome") or "")
        if oc == "ok":
            ok += 1
        elif oc:
            bad += 1
        by_intent[str(r.get("intent") or "?")] += 1
        sk = r.get("skill") or r.get("skill_name")
        if sk:
            by_skill[str(sk)] += 1
    return {
        "window_hours": hours,
        "ok": ok,
        "bad": bad,
        "top_intents": by_intent.most_common(5),
        "top_skills": by_skill.most_common(8),
    }


def _route_risk_stats(hours: float = 24.0) -> Dict[str, Any]:
    from core.route_risk_memory import stumble_from_turn_quality_loop

    path = _runtime_dir() / "route_risk.jsonl"
    cutoff = time.time() - hours * 3600
    rows = _tail_jsonl(path, 400)
    n_total = 0
    n_ql = 0
    by_err: Counter[str] = Counter()
    by_mod: Counter[str] = Counter()
    from core.route_risk_cluster import record_ts_epoch

    for r in rows:
        t = record_ts_epoch(r)
        if t <= 0 or t < cutoff:
            continue
        n_total += 1
        det = str(r.get("detail") or "")
        if stumble_from_turn_quality_loop(det):
            n_ql += 1
            continue
        by_err[str(r.get("error_type") or "unknown")] += 1
        by_mod[str(r.get("module") or "?")] += 1
    n_route = max(0, n_total - n_ql)
    return {
        "window_hours": hours,
        "stumbles": n_route,
        "stumbles_total": n_total,
        "quality_loop_stumbles": n_ql,
        "by_error_type": by_err.most_common(6),
        "by_module": by_mod.most_common(6),
    }


def _skill_reputation_top(user_id: Optional[str] = None, limit: int = 12) -> List[Dict[str, Any]]:
    try:
        from core.agent_kv.store import agent_kv_branch, agent_kv_enabled, iter_prefix

        if not agent_kv_enabled():
            return []
        uid = str(user_id or "").strip()
        if not uid:
            return []
        prefix = f"{uid}|"
        rows = {k: v for k, v in iter_prefix("reputation_skill", prefix, branch=agent_kv_branch())}
        from core.admin_reputation_view import build_admin_reputation_payload

        payload = build_admin_reputation_payload(uid, {}, rows, branch=agent_kv_branch())
        return list(payload.get("reputation_skills") or [])[:limit]
    except Exception:
        return []


def build_learning_digest(*, user_id: Optional[str] = None, hours: float = 24.0) -> Dict[str, Any]:
    """Агрегат для /admin_learning_digest и кнопки «Дайджест обучения»."""
    try:
        from core.admin_autonomy import build_autonomy_report

        autonomy = build_autonomy_report()
    except Exception as e:
        autonomy = {"error": str(e)[:120]}
    try:
        from core.route_risk_cluster import cluster_route_risk_recent

        clusters = cluster_route_risk_recent(hours=min(hours, 48.0), min_count=2)
    except Exception as e:
        clusters = {"error": str(e)[:120]}
    stagnation: Dict[str, Any] = {}
    try:
        from core.learning_stagnation import detect_stagnation

        stagnation = detect_stagnation()
    except Exception as e:
        stagnation = {"error": str(e)[:120]}
    exp_rules: Dict[str, Any] = {}
    try:
        from core.experience_rules import extract_rules_from_experience

        exp_rules = {"rules": extract_rules_from_experience(hours=hours)}
    except Exception as e:
        exp_rules = {"error": str(e)[:120]}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "user_id": user_id,
        "lessons": _lesson_stats(),
        "experience": _experience_stats(hours),
        "route_risk": _route_risk_stats(hours),
        "skill_reputation": _skill_reputation_top(user_id),
        "autonomy": autonomy,
        "error_clusters": clusters,
        "stagnation": stagnation,
        "experience_rules": exp_rules,
        "hints": {
            "feedback": "Пользователь: кнопки 👍/👎 под ответом; админ: /admin_reputation, /admin_reputation_json",
            "self_play": "GVU/Self-Play/LoRA — не включены; см. ROUTE_RISK_CLUSTER_AUTO_LESSON",
        },
    }


def format_learning_digest_html(digest: Dict[str, Any]) -> str:
    from core.telegram_ui import esc

    lines = [
        "🧠 <b>Дайджест обучения</b>",
        f"<i>Окно опыта/route_risk: {esc(str((digest.get('experience') or {}).get('window_hours', 24)))} ч</i>",
        "",
    ]
    les = digest.get("lessons") or {}
    if isinstance(les, dict):
        lines.append(
            f"📚 Уроки: активных <b>{esc(str(les.get('active_lessons', '?')))}</b>"
            + (
                f", ср. эффективность <b>{esc(str(les.get('avg_effectiveness')))}</b>"
                if les.get("avg_effectiveness") is not None
                else ""
            )
        )
    exp = digest.get("experience") or {}
    if isinstance(exp, dict):
        lines.append(
            f"✅ Опыт: ok <b>{exp.get('ok', 0)}</b> · сбои <b>{exp.get('bad', 0)}</b>"
        )
        tops = exp.get("top_skills") or []
        if tops:
            sk = ", ".join(f"{a}({c})" for a, c in tops[:5])
            lines.append(f"🎯 Скиллы в опыте: <code>{esc(sk)}</code>")
    rr = digest.get("route_risk") or {}
    if isinstance(rr, dict):
        sr = int(rr.get("stumbles") or 0)
        ql = int(rr.get("quality_loop_stumbles") or 0)
        if ql > 0:
            lines.append(
                f"⚠️ Route risk: <b>{sr}</b> stumble за окно (маршрутизация; "
                f"+<b>{ql}</b> от <code>quality_loop</code> — см. "
                f"<code>docs/TURN_QUALITY_LOOP_RU.md</code>)"
            )
        else:
            lines.append(f"⚠️ Route risk: <b>{sr}</b> stumble за окно")
    skills = digest.get("skill_reputation") or []
    if skills:
        lines.append("")
        lines.append("<b>Топ скиллов (v_c):</b>")
        for s in skills[:6]:
            if isinstance(s, dict):
                lines.append(
                    f"• <code>{esc(str(s.get('skill', '?')))}</code> "
                    f"v_c={esc(str(s.get('v_c', '?')))} ok={esc(str(s.get('n_ok', 0)))}"
                )
    stg = digest.get("stagnation") or {}
    if isinstance(stg, dict) and stg.get("stagnation") is not None:
        lines.append(
            f"📉 Стагнация v_c: <b>{'да' if stg.get('stagnation') else 'нет'}</b> "
            f"(Δ={esc(str(stg.get('delta', '?')))})"
        )
    exp_r = digest.get("experience_rules") or {}
    if isinstance(exp_r, dict) and exp_r.get("rules"):
        lines.append(f"📐 Правил из experience: <b>{len(exp_r['rules'])}</b>")
    cl = digest.get("error_clusters") or {}
    if isinstance(cl, dict) and cl.get("clusters"):
        lines.append("")
        lines.append(f"<b>Кластеры ошибок:</b> {len(cl['clusters'])}")
        for c in (cl.get("clusters") or [])[:4]:
            if isinstance(c, dict):
                lines.append(
                    f"• {esc(str(c.get('error_type', '?')))} / {esc(str(c.get('intent', '?')))} "
                    f"×{esc(str(c.get('count', 0)))}"
                )
    lines.append("")
    lines.append("<i>Полный JSON: <code>/admin_learning_digest_json</code> [user_id]</i>")
    return "\n".join(lines)
