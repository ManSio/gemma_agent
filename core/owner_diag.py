"""
Краткая сводка здоровья gemma_bot (read-only) — CLI, Cursor, /diag в Telegram.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.telegram_util import sanitize_html

MUST_OFF = (
    "TURN_QUALITY_LOOP_ENABLED",
    "TURN_QUALITY_AUTO_PENDING_CORRECTION",
    "MCE_ENABLED",
    "MCE_AUTO_APPLY",
    "GOAL_RUNNER_AUTO_START",
    "ROUTER_PASSIVE_ENABLED",
)
MUST_ON = ("BRAIN_OPERATOR_CORRECTIONS_IN_HINT",)


def env_flag(key: str) -> str:
    v = (os.getenv(key) or "").strip().lower()
    return "on" if v in ("1", "true", "yes", "on") else "off"


def _truthy(name: str) -> bool:
    return env_flag(name) == "on"


def _parse_ts(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t
    except ValueError:
        return None


def _read_json(path: Path) -> Optional[dict]:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None


def _tail_jsonl(path: Path, n: int = 150) -> List[dict]:
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    out: List[dict] = []
    for ln in lines[-n:]:
        try:
            d = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


def _analyze_turns_tail(rows: List[dict]) -> Dict[str, Any]:
    issues: Counter = Counter()
    n = 0
    last_ts = ""
    for d in rows:
        if d.get("type") == "scenario":
            continue
        n += 1
        ts = str(d.get("ts") or "")
        if ts:
            last_ts = ts
        for i in d.get("issues") or []:
            issues[str(i)] += 1
        ast = str(d.get("assistant_excerpt") or d.get("assistant_text") or "")
        if "TOOL_CALL:" in ast:
            issues["leak_TOOL_CALL"] += 1
    return {"n": n, "last_ts": last_ts, "issues": dict(issues.most_common(8))}


def _heuristic_misses_tail(path: Path, n: int = 3) -> List[Dict[str, str]]:
    rows = _tail_jsonl(path, n)
    out: List[Dict[str, str]] = []
    for d in rows[-n:]:
        out.append(
            {
                "ts": str(d.get("ts") or "")[:16],
                "rule_id": str(d.get("rule_id") or ""),
                "verdict": str(d.get("verdict") or ""),
            }
        )
    return out


def project_root(explicit: Optional[Path] = None) -> Path:
    if explicit is not None:
        return explicit
    return Path(os.getenv("GEMMA_PROJECT_ROOT") or Path(__file__).resolve().parents[1])


def collect_owner_diag(root: Optional[Path] = None) -> Dict[str, Any]:
    """Собрать сводку без subprocess (быстро, для /diag в TG)."""
    project = project_root(root)
    data = project / "data"
    runtime = data / "runtime"
    problems: List[str] = []
    checks: List[Dict[str, Any]] = []

    out: Dict[str, Any] = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "project_root": str(project),
        "checks": checks,
        "problems": problems,
    }

    boot = _read_json(runtime / "boot_state.json")
    out["boot"] = boot or {}
    out["autopilot"] = {
        "mode": _truthy("GEMMA_AUTOPILOT_MODE"),
        "digest_hours_utc": (os.getenv("AUTOPILOT_DIGEST_HOURS_UTC") or "8,20").strip(),
    }

    for k in MUST_OFF:
        ok = env_flag(k) == "off"
        checks.append({"id": k, "ok": ok, "want": "off", "value": env_flag(k)})
        if not ok:
            problems.append(f"{k} должен быть выкл")

    for k in MUST_ON:
        ok = env_flag(k) == "on"
        checks.append({"id": k, "ok": ok, "want": "on", "value": env_flag(k)})
        if not ok:
            problems.append(f"{k} должен быть вкл")

    files = {
        "turns": runtime / "turns.jsonl",
        "ops_trace": runtime / "ops_trace.jsonl",
        "heuristic_misses": runtime / "heuristic_misses.jsonl",
    }
    out["files"] = {name: p.is_file() for name, p in files.items()}

    turns_rows = _tail_jsonl(files["turns"], 120)
    out["turns_tail"] = _analyze_turns_tail(turns_rows)
    if out["turns_tail"].get("issues", {}).get("leak_TOOL_CALL"):
        problems.append("утечка TOOL_CALL в хвосте turns")

    if files["turns"].is_file():
        try:
            from core.research.reliability_horizon import compute_horizon_report

            hz = compute_horizon_report(files["turns"], days=3)
            out["reliability_horizon"] = {
                "horizon_turns_50pct": hz.get("horizon_turns_50pct"),
                "sessions_n": hz.get("sessions_n"),
                "interpretation": hz.get("interpretation", "")[:200],
            }
        except Exception:
            out["reliability_horizon"] = None

    mem_tags = 0
    corr_pending = 0
    for row in turns_rows:
        if isinstance(row, dict):
            if row.get("correction_pending"):
                corr_pending += 1
            if row.get("policy_hint_tags"):
                mem_tags += 1
    if turns_rows:
        out["memory_turns"] = {
            "with_policy_hints": mem_tags,
            "correction_pending": corr_pending,
        }

    out["heuristic_misses_tail"] = _heuristic_misses_tail(files["heuristic_misses"], 3)
    out["ok"] = len(problems) == 0
    out["problem_count"] = len(problems)
    return out


def format_owner_diag_markdown(st: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("Gemma — сводка состояния")
    lines.append(f"UTC: {st.get('ts_utc', '')[:19]}")
    boot = st.get("boot") or {}
    if boot.get("last_start_utc"):
        lines.append(f"Старт бота: {boot.get('last_start_utc', '')[:19]}")
        if boot.get("restart_detected"):
            lines.append("[!] недавний restart_detected")
    ap = st.get("autopilot") or {}
    if ap.get("mode"):
        lines.append(f"Автопилот: вкл, дайджесты UTC {ap.get('digest_hours_utc')}")
    else:
        lines.append("Автопилот: выкл (ночные отчёты — GEMMA_AUTOPILOT_MODE=on)")
    for c in st.get("checks") or []:
        if c.get("id") in MUST_OFF or c.get("id") in MUST_ON:
            mark = "OK" if c.get("ok") else "!!"
            lines.append(f"  [{mark}] {c.get('id')}: {c.get('value')}")
    tt = st.get("turns_tail") or {}
    if tt.get("n"):
        lines.append(f"Ходов в хвосте turns: {tt.get('n')}")
        for k, v in (tt.get("issues") or {}).items():
            lines.append(f"  — {k}: {v}")
    lines.append(
        "Итог: ок" if st.get("ok") else f"Внимание: {st.get('problem_count')} пункт(ов)"
    )
    for p in st.get("problems") or []:
        lines.append(f"  • {p}")
    lines.append("Подробно: /admin_xray · архив: /admin_diagnostic")
    return "\n".join(lines)


def format_owner_diag_html(st: Dict[str, Any]) -> str:
    """Короткий HTML для Telegram (admin /diag)."""
    ok = bool(st.get("ok"))
    title = "Диагностика Gemma" if ok else "Диагностика Gemma — есть замечания"
    lines: List[str] = [f"<b>{sanitize_html(title)}</b>"]
    lines.append(f"<i>UTC {sanitize_html(str(st.get('ts_utc', ''))[:19])}</i>")

    boot = st.get("boot") or {}
    if boot.get("last_start_utc"):
        lines.append(f"Старт: <code>{sanitize_html(str(boot.get('last_start_utc'))[:19])}</code>")
        if boot.get("restart_detected"):
            lines.append("Статус: <i>недавний перезапуск</i>")
    else:
        lines.append("Старт: <i>нет boot_state</i>")

    ap = st.get("autopilot") or {}
    if ap.get("mode"):
        lines.append(
            f"Автопилот: <b>вкл</b> · дайджесты UTC <code>{sanitize_html(str(ap.get('digest_hours_utc')))}</code>"
        )
    else:
        lines.append("Автопилот: <i>выкл</i> — ночные сводки: <code>GEMMA_AUTOPILOT_MODE=on</code>")

    bad_flags = [c for c in (st.get("checks") or []) if not c.get("ok")]
    if bad_flags:
        lines.append("<b>Флаги:</b>")
        for c in bad_flags[:6]:
            lines.append(
                f"• <code>{sanitize_html(str(c.get('id')))}</code> = {sanitize_html(str(c.get('value')))}"
            )
    else:
        lines.append("Флаги прод: <b>OK</b>")

    files = st.get("files") or {}
    miss = [k for k, v in files.items() if not v]
    if miss:
        lines.append(f"Журналы: нет {', '.join(miss)}")
    else:
        lines.append("Журналы turns/ops: <b>есть</b>")

    tt = st.get("turns_tail") or {}
    if tt.get("issues"):
        parts = [f"{sanitize_html(k)}×{v}" for k, v in list(tt.get("issues", {}).items())[:5]]
        lines.append("Хвост turns: " + ", ".join(parts))
    elif tt.get("n"):
        lines.append(f"Хвост turns: {int(tt.get('n'))} ходов, без issues")

    for p in (st.get("problems") or [])[:4]:
        lines.append(f"[!] {sanitize_html(p)}")

    lines.append(
        "<blockquote><i>Рентген: /admin_xray · ZIP: /admin_diagnostic · "
        "привычки: /admin_usage_digest</i></blockquote>"
    )
    return "\n".join(lines)
