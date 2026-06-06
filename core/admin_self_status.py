"""Сводка PRODUCT_FINISH + ops-метрики для /admin_self."""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import List

from core.admin_ops_metrics import (
    collect_admin_self_metrics,
    format_ms,
    format_recent_ab_counts,
)
from core.runtime_telegram_settings import effective_bool
from core.telegram_ui import esc

logger = logging.getLogger(__name__)

_PROD_ENV_KEYS = (
    "MCE_AUTO_APPLY",
    "GOAL_RUNNER_AUTO_START",
    "ROUTER_PASSIVE_ENABLED",
    "LLM_TRIAGE_ENABLED",
    "ROUTE_RISK_CLUSTER_AUTO_LESSON",
    "TELEGRAM_PIPELINE_PRIVATE_PARALLEL",
    "PRE_LLM_PLAN_ENABLED",
    "BRAIN_KV_PROFILE_STICKY",
    "BRAIN_OPERATOR_CORRECTIONS_IN_HINT",
)


def _git_head(root: Path) -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception as e:
        logger.debug("%s optional failed: %s", "admin_self_status", e, exc_info=True)
    return "?"


def _env_line(key: str, *, default: str = "") -> str:
    v = (os.getenv(key) or default).strip()
    return f"{key}={v or '—'}"


def _append_metrics_block(lines: List[str], root: Path) -> None:
    try:
        hours_raw = (os.getenv("ADMIN_SELF_METRICS_HOURS") or "24").strip()
        hours = max(1.0, min(168.0, float(hours_raw)))
    except ValueError:
        hours = 24.0

    m = collect_admin_self_metrics(root, hours=hours)
    live = m.get("live") or {}
    llm = m.get("llm_24h") or {}
    turns = m.get("turns_24h") or {}

    lines.append(f"<b>Метрики ({int(hours)}h лог + с boot)</b>")

    if live.get("available"):
        tg = format_ms(live.get("telegram_p95_ms"))
        or_p = format_ms(live.get("openrouter_p95_ms"))
        ok = int(live.get("llm_ok") or 0)
        fail = int(live.get("llm_fail") or 0)
        n_in = int(live.get("input_messages") or 0)
        samples = int(live.get("telegram_samples") or 0)
        lines.append(
            f"<code>boot tg_p95={tg} or_p95={or_p} llm={ok}ok/{fail}fail in={n_in} n={samples}</code>"
        )
    else:
        lines.append("<i>boot: мало данных (перезапуск или нет ходов)</i>")

    if llm.get("available"):
        kv = llm.get("kv_hit_pct")
        kv_s = f"{kv:.0f}%" if kv is not None else "—"
        fail_total = int(llm.get("llm_fail") or 0)
        ok_total = int(llm.get("llm_ok") or 0)
        brain_n = int(llm.get("brain_rows") or 0)
        p50 = format_ms(llm.get("brain_latency_p50_ms"))
        p95 = format_ms(llm.get("brain_latency_p95_ms"))
        recent = format_recent_ab_counts(llm.get("recent_brain_counts") or {})
        lines.append(
            f"<code>log brain={brain_n} kv={kv_s} p50={p50} p95={p95} llm_fail={fail_total}/{ok_total + fail_total}</code>"
        )
        if recent != "—":
            lines.append(f"<code>C6 recent: {esc(recent)}</code>")
    else:
        lines.append("<i>log llm_usage: нет данных за окно</i>")

    if turns.get("available"):
        tp50 = format_ms(turns.get("latency_p50_ms"))
        tp95 = format_ms(turns.get("latency_p95_ms"))
        t_n = int(turns.get("turns") or 0)
        t_iss = int(turns.get("issues") or 0)
        lines.append(f"<code>turns n={t_n} iss={t_iss} p50={tp50} p95={tp95}</code>")

    lines.append("")


def build_admin_self_html(*, project_root: str | None = None) -> str:
    root = Path(project_root or os.getenv("GEMMA_PROJECT_ROOT") or ".").resolve()
    lines: List[str] = [
        "<b>Gemma — self-status</b>",
        f"<code>git {esc(_git_head(root))}</code>",
        "",
        "<b>Prod flags</b>",
    ]
    for key in _PROD_ENV_KEYS:
        default = "1" if key == "TELEGRAM_PIPELINE_PRIVATE_PARALLEL" else ""
        lines.append(f"<code>{esc(_env_line(key, default=default))}</code>")
    try:
        _par = int((os.getenv("TELEGRAM_PIPELINE_PRIVATE_PARALLEL") or "1").strip() or "1")
        if _par > 1:
            lines.append(
                "<b>⚠️ TELEGRAM_PIPELINE_PRIVATE_PARALLEL&gt;1</b> — риск ответа «на прошлый вопрос» (20.05)"
            )
    except ValueError:
        pass

    lines.extend(
        [
            "",
            "<b>Brain / context</b>",
            f"<code>{esc(_env_line('BRAIN_DIRECT_DIALOG_ENABLED'))}</code>",
            f"<code>{esc(_env_line('BRAIN_CHAT_AGENT_MODE'))}</code>",
            f"<code>{esc(_env_line('BRAIN_STANDARD_RECENT_COUNT'))}</code>",
            f"<code>direct={effective_bool('BRAIN_DIRECT_DIALOG_ENABLED', default=False)}</code>",
            f"<code>chat_agent={effective_bool('BRAIN_CHAT_AGENT_MODE', default=False)}</code>",
            "",
        ]
    )

    _append_metrics_block(lines, root)

    try:
        from core.turn_observer import read_recent_turns

        rows = read_recent_turns(limit=8, issues_only=True)
        if rows:
            lines.append("<b>Последние issues (turns)</b>")
            for r in rows[-5:]:
                ts = str(r.get("ts") or "")[:16]
                iss = ",".join(r.get("issues") or []) or "—"
                ue = esc(str(r.get("user_excerpt") or "")[:40])
                lines.append(f"• <code>{ts}</code> <code>{esc(iss)}</code> {ue}")
        else:
            lines.append("<i>issues в turns.jsonl за последние ходы — нет</i>")
    except Exception as e:
        lines.append(f"<i>turns: {esc(str(e))}</i>")

    lines.append("")
    try:
        p = root / "data" / "runtime" / "user_issues.jsonl"
        if p.is_file():
            neg = 0
            for ln in p.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]:
                if '"score": -1' in ln:
                    neg += 1
            lines.append(f"<b>👎 в журнале (хвост 200 строк):</b> {neg}")
    except Exception as e:
        logger.debug("%s optional failed: %s", "admin_self_status", e, exc_info=True)

    lines.append("")
    lines.append(
        "<i>Детали: /admin_llm_usage · /admin_pulse · "
        "docs/PRODUCT_FINISH_TELEGRAM_CHECKLIST_RU.md</i>"
    )
    return "\n".join(lines)
