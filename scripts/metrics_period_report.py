#!/usr/bin/env python3
"""
Сводка метрик по дням и фазам: слой «агент» (ops_trace/turns) vs «LLM» (llm_usage) + корреляция.

  python scripts/metrics_period_report.py
  python scripts/metrics_period_report.py --root /opt/gemma_agent \\
    --json data/benchmarks/metrics_periods_latest.json \\
    --history data/benchmarks/metrics_snapshots.jsonl \\
    --out docs/METRICS_PERIODS_RU.md

История: каждый запуск с --history дописывает JSONL-снимок (schema v2).
Реестр полей: config/metrics_period_registry.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, DefaultDict, Dict, Iterator, List, Optional, Tuple

SCHEMA_VERSION = 2
CORRELATION_WINDOW_SEC = 120.0

_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "config" / "metrics_period_registry.json"


def _load_registry() -> Dict[str, Any]:
    if _REGISTRY_PATH.is_file():
        try:
            return json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def _product_phases(registry: Dict[str, Any]) -> List[Tuple[str, str, str, str]]:
    raw = registry.get("product_phases") or []
    out: List[Tuple[str, str, str, str]] = []
    for p in raw:
        if isinstance(p, dict) and p.get("id"):
            out.append(
                (str(p["id"]), str(p["start"]), str(p["end"]), str(p.get("label") or p["id"]))
            )
    if out:
        return out
    return [
        ("bootstrap", "2026-05-02", "2026-05-14", "Старт"),
        ("current", "2026-05-23", "2099-12-31", "Сейчас"),
    ]


def _reference_maps(registry: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    ref = registry.get("reference_overrides") or {}
    out: Dict[str, Dict[str, float]] = {}
    for key, mapping in ref.items():
        if isinstance(mapping, dict):
            out[key] = {str(d): float(v) for d, v in mapping.items()}
    return out


def _parse_ts(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    try:
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(float(raw), tz=timezone.utc)
        s = str(raw).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def _read_jsonl(path: Path) -> Iterator[Dict[str, Any]]:
    if not path.is_file():
        return
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            o = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if isinstance(o, dict):
            yield o


def _p95(vals: List[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    i = min(len(s) - 1, int(math.ceil(0.95 * len(s)) - 1))
    return float(s[max(0, i)])


def _median(vals: List[float]) -> float:
    return float(statistics.median(vals)) if vals else 0.0


def _phase_for_date(day: str, phases: List[Tuple[str, str, str, str]]) -> str:
    for pid, start, end, _ in phases:
        if start <= day <= end:
            return pid
    return "other"


def _pct(num: float, den: float) -> Optional[float]:
    if den <= 0:
        return None
    return round(100.0 * num / den, 1)


@dataclass
class AgentDay:
    turns_n: int = 0
    latencies: List[float] = field(default_factory=list)
    ok_n: int = 0
    fast_path_n: int = 0
    no_llm_n: int = 0
    math_n: int = 0
    issues_total: int = 0

    def to_dict(self) -> Dict[str, Any]:
        n = self.turns_n
        return {
            "turns_n": n,
            "latency_ms_median": round(_median(self.latencies), 1) if self.latencies else None,
            "latency_ms_p95": round(_p95(self.latencies), 1) if self.latencies else None,
            "ok_pct": _pct(self.ok_n, n),
            "fast_path_pct": _pct(self.fast_path_n, n),
            "no_llm_module_pct": _pct(self.no_llm_n, n),
            "math_module_pct": _pct(self.math_n, n),
            "issues_per_turn": round(self.issues_total / n, 2) if n else None,
        }


@dataclass
class LlmDay:
    calls_n: int = 0
    calls_ok: int = 0
    latencies: List[float] = field(default_factory=list)
    prompt_tokens: int = 0
    cached_prompt: int = 0
    brain_first_prompt: List[int] = field(default_factory=list)
    brain_first_lat: List[float] = field(default_factory=list)
    brain_second_n: int = 0
    router_n: int = 0
    by_tag_lat: DefaultDict[str, List[float]] = field(default_factory=lambda: defaultdict(list))
    by_tag_n: Counter = field(default_factory=Counter)

    def to_dict(self, *, refs: Dict[str, Dict[str, float]], day: str, use_reference: bool) -> Dict[str, Any]:
        bf_med = _median([float(x) for x in self.brain_first_prompt])
        src = "local"
        ref_map = refs.get("brain_first_median_prompt") or {}
        if use_reference and day in ref_map and (not self.brain_first_prompt or len(self.brain_first_prompt) < 5):
            bf_med = ref_map[day]
            src = "reference"
        tag_stats = {}
        for tag, lats in sorted(self.by_tag_lat.items()):
            tag_stats[tag] = {
                "n": self.by_tag_n[tag],
                "latency_ms_median": round(_median(lats), 1) if lats else None,
                "latency_ms_p95": round(_p95(lats), 1) if lats else None,
            }
        return {
            "calls_n": self.calls_n,
            "calls_ok": self.calls_ok,
            "latency_ms_median": round(_median(self.latencies), 1) if self.latencies else None,
            "latency_ms_p95": round(_p95(self.latencies), 1) if self.latencies else None,
            "prompt_tokens_sum": self.prompt_tokens,
            "brain_first_median_prompt": bf_med if bf_med else None,
            "brain_first_prompt_source": src,
            "brain_first_n": len(self.brain_first_prompt),
            "brain_first_latency_p95_ms": round(_p95(self.brain_first_lat), 1) if self.brain_first_lat else None,
            "brain_second_n": self.brain_second_n,
            "router_n": self.router_n,
            "kv_cache_token_pct": round(100.0 * self.cached_prompt / self.prompt_tokens, 2)
            if self.prompt_tokens
            else None,
            "by_tag": tag_stats,
        }


@dataclass
class PipelineDay:
    llm_sums: List[float] = field(default_factory=list)
    agent_latencies: List[float] = field(default_factory=list)
    shares: List[float] = field(default_factory=list)
    overheads: List[float] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        n = len(self.shares)
        return {
            "turns_with_correlation_n": n,
            "llm_share_of_turn_pct": round(_median(self.shares), 1) if self.shares else None,
            "overhead_ms_median": round(_median(self.overheads), 1) if self.overheads else None,
            "llm_sum_ms_median": round(_median(self.llm_sums), 1) if self.llm_sums else None,
            "agent_turn_ms_median": round(_median(self.agent_latencies), 1) if self.agent_latencies else None,
        }


@dataclass
class QualityDay:
    cdc_total: int = 0
    cdc_ok: int = 0
    cdc_clarify: int = 0
    cdc_fail: int = 0
    feedback_neg: int = 0

    def to_dict(self, *, refs: Dict[str, Dict[str, float]], day: str, use_reference: bool) -> Dict[str, Any]:
        ok_pct = _pct(self.cdc_ok, self.cdc_total)
        ref = (refs.get("cdc_ok_pct") or {}).get(day)
        if use_reference and ref is not None and ok_pct is None:
            ok_pct = ref
        return {
            "cdc_total": self.cdc_total,
            "cdc_ok_pct": ok_pct,
            "cdc_clarify": self.cdc_clarify,
            "cdc_fail": self.cdc_fail,
            "feedback_neg": self.feedback_neg,
        }


def _is_fast_path(reasoning: Any) -> bool:
    if not isinstance(reasoning, dict):
        return False
    reason = str(reasoning.get("reason") or "").lower()
    mode = str(reasoning.get("mode") or "").lower()
    return reason == "fast_path" or mode == "fast_path"


def _plan_no_llm(steps: Any) -> bool:
    if not isinstance(steps, list) or not steps:
        return False
    s = {str(x).lower().replace("_", "-") for x in steps}
    if "math" in s or "light-reminders" in s or "schedule" in s:
        return True
    if s == {"chat-orchestrator"}:
        return False
    return len(s) == 1 and "chat-orchestrator" not in s


def _ingest_agent_row(row: Dict[str, Any], agent: AgentDay) -> None:
    if str(row.get("type") or "turn") != "turn":
        return
    agent.turns_n += 1
    lat = row.get("latency_ms")
    if lat is not None:
        try:
            agent.latencies.append(float(lat))
        except (TypeError, ValueError):
            pass
    if row.get("ok") is True:
        agent.ok_n += 1
    if _is_fast_path(row.get("reasoning")):
        agent.fast_path_n += 1
    steps = row.get("plan_steps")
    if _plan_no_llm(steps):
        agent.no_llm_n += 1
    if isinstance(steps, list) and any("math" in str(s).lower() for s in steps):
        agent.math_n += 1
    issues = row.get("issues")
    if isinstance(issues, list):
        agent.issues_total += len(issues)


def _llm_paths(root: Path) -> List[Path]:
    paths = [root / "data" / "runtime" / "llm_usage.jsonl", root / "data" / "llm_usage.jsonl"]
    env = (os.getenv("GEMMA_LLM_USAGE_PATH") or "").strip()
    if env:
        paths.insert(0, Path(env))
    seen: set[str] = set()
    out: List[Path] = []
    for p in paths:
        key = str(p.resolve()) if p.exists() else str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _load_llm(root: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in _llm_paths(root):
        rows.extend(_read_jsonl(path))
    seen: set[str] = set()
    uniq: List[Dict[str, Any]] = []
    for r in rows:
        key = "|".join(str(r.get(k, "")) for k in ("ts", "tag", "prompt_tokens", "latency_ms", "session_id"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    return uniq


def _index_llm_by_day(llm_rows: List[Dict[str, Any]]) -> Dict[str, List[Tuple[float, str, float]]]:
    """day -> [(ts_epoch, user_id, latency_ms), ...]"""
    out: DefaultDict[str, List[Tuple[float, str, float]]] = defaultdict(list)
    for r in llm_rows:
        if not r.get("ok"):
            continue
        dt = _parse_ts(r.get("ts"))
        if not dt:
            continue
        day = dt.strftime("%Y-%m-%d")
        lat = float(r.get("latency_ms") or 0)
        if lat <= 0:
            continue
        uid = ""
        sid = str(r.get("session_id") or "")
        m = re.match(r"u-(\d+)\.", sid)
        if m:
            uid = m.group(1)
        out[day].append((dt.timestamp(), uid, lat))
    return dict(out)


def _correlate_turn(
    turn_ts: float,
    user_id: str,
    agent_lat: float,
    llm_events: List[Tuple[float, str, float]],
) -> Optional[Tuple[float, float, float]]:
    """Returns (llm_sum, share_pct, overhead) or None."""
    matched = [
        lat
        for ts, uid, lat in llm_events
        if abs(ts - turn_ts) <= CORRELATION_WINDOW_SEC and (not user_id or not uid or uid == user_id)
    ]
    if not matched:
        return None
    llm_sum = sum(matched)
    if agent_lat <= 0:
        return None
    share = min(100.0, 100.0 * llm_sum / agent_lat)
    overhead = max(0.0, agent_lat - llm_sum)
    return llm_sum, share, overhead


def _aggregate_phase(
    daily_rows: List[Dict[str, Any]],
    layer_key: str,
) -> Dict[str, Any]:
    """Средние/медианы по фазе для вложенного слоя."""
    sub = [r.get(layer_key) or {} for r in daily_rows]
    if not any(sub):
        return {}

    def med_field(field: str) -> Optional[float]:
        vals = [x[field] for x in sub if x.get(field) is not None]
        return round(_median([float(v) for v in vals]), 1) if vals else None

    out: Dict[str, Any] = {
        "days": len(sub),
        "latency_ms_median": med_field("latency_ms_median"),
        "latency_ms_p95": med_field("latency_ms_p95"),
    }
    if layer_key == "agent":
        out["turns_n"] = sum(x.get("turns_n") or 0 for x in sub)
        out["ok_pct"] = med_field("ok_pct")
        out["fast_path_pct"] = med_field("fast_path_pct")
        out["math_module_pct"] = med_field("math_module_pct")
    elif layer_key == "llm":
        out["calls_n"] = sum(x.get("calls_n") or 0 for x in sub)
        out["brain_first_median_prompt"] = med_field("brain_first_median_prompt")
        out["kv_cache_token_pct"] = med_field("kv_cache_token_pct")
        out["brain_second_n"] = sum(x.get("brain_second_n") or 0 for x in sub)
    elif layer_key == "pipeline":
        out["llm_share_of_turn_pct"] = med_field("llm_share_of_turn_pct")
        out["overhead_ms_median"] = med_field("overhead_ms_median")
        out["turns_with_correlation_n"] = sum(x.get("turns_with_correlation_n") or 0 for x in sub)
    elif layer_key == "quality":
        out["cdc_ok_pct"] = med_field("cdc_ok_pct")
        out["feedback_neg"] = sum(x.get("feedback_neg") or 0 for x in sub)
    return out


def collect(root: Path, *, use_reference: bool) -> Dict[str, Any]:
    registry = _load_registry()
    phases = _product_phases(registry)
    refs = _reference_maps(registry)

    agent_days: DefaultDict[str, AgentDay] = defaultdict(AgentDay)
    llm_days: DefaultDict[str, LlmDay] = defaultdict(LlmDay)
    pipe_days: DefaultDict[str, PipelineDay] = defaultdict(PipelineDay)
    qual_days: DefaultDict[str, QualityDay] = defaultdict(QualityDay)

    runtime = root / "data" / "runtime"
    ops_path = runtime / "ops_trace.jsonl"
    env_ops = (os.getenv("GEMMA_OPS_TRACE_PATH") or "").strip()
    if env_ops:
        ops_path = Path(env_ops)

    for r in _read_jsonl(ops_path):
        dt = _parse_ts(r.get("ts"))
        if not dt:
            continue
        day = dt.strftime("%Y-%m-%d")
        _ingest_agent_row(r, agent_days[day])

    for r in _read_jsonl(runtime / "turns.jsonl"):
        dt = _parse_ts(r.get("ts"))
        if not dt:
            continue
        day = dt.strftime("%Y-%m-%d")
        ad = agent_days[day]
        if ad.turns_n == 0:
            ad.turns_n += 1
            lm = r.get("latency_ms")
            if lm is not None:
                try:
                    ad.latencies.append(float(lm))
                except (TypeError, ValueError):
                    pass

    llm_rows = _load_llm(root)
    llm_by_day_index = _index_llm_by_day(llm_rows)

    for r in llm_rows:
        dt = _parse_ts(r.get("ts"))
        if not dt:
            continue
        day = dt.strftime("%Y-%m-%d")
        ld = llm_days[day]
        ld.calls_n += 1
        if r.get("ok"):
            ld.calls_ok += 1
        pt = int(r.get("prompt_tokens") or 0)
        ld.prompt_tokens += pt
        ld.cached_prompt += int(r.get("cached_prompt_tokens") or 0)
        lat = float(r.get("latency_ms") or 0)
        if lat > 0:
            ld.latencies.append(lat)
        tag = str(r.get("tag") or "unknown")
        ld.by_tag_n[tag] += 1
        if lat > 0:
            ld.by_tag_lat[tag].append(lat)
        if tag == "brain_first":
            ld.brain_first_prompt.append(pt)
            if lat > 0:
                ld.brain_first_lat.append(lat)
        elif tag == "brain_second":
            ld.brain_second_n += 1
        elif tag == "router_classifier":
            ld.router_n += 1

    for r in _read_jsonl(runtime / "cdc_turn_outcomes.jsonl"):
        dt = _parse_ts(r.get("ts"))
        if not dt:
            continue
        day = dt.strftime("%Y-%m-%d")
        qd = qual_days[day]
        qd.cdc_total += 1
        oc = str(r.get("outcome") or "")
        if oc == "ok":
            qd.cdc_ok += 1
        elif oc == "clarify":
            qd.cdc_clarify += 1
        elif oc in ("fail", "error"):
            qd.cdc_fail += 1

    for r in _read_jsonl(runtime / "user_feedback.jsonl"):
        dt = _parse_ts(r.get("ts"))
        if not dt:
            continue
        day = dt.strftime("%Y-%m-%d")
        try:
            score = int(r.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        if score < 0:
            qual_days[day].feedback_neg += 1

    # Корреляция agent turn ↔ LLM в окне
    for r in _read_jsonl(ops_path):
        if str(r.get("type") or "turn") != "turn":
            continue
        dt = _parse_ts(r.get("ts"))
        if not dt:
            continue
        day = dt.strftime("%Y-%m-%d")
        try:
            agent_lat = float(r.get("latency_ms") or 0)
        except (TypeError, ValueError):
            continue
        if agent_lat <= 0:
            continue
        corr = _correlate_turn(
            dt.timestamp(),
            str(r.get("user_id") or ""),
            agent_lat,
            llm_by_day_index.get(day, []),
        )
        if corr:
            llm_sum, share, overhead = corr
            pd = pipe_days[day]
            pd.llm_sums.append(llm_sum)
            pd.agent_latencies.append(agent_lat)
            pd.shares.append(share)
            pd.overheads.append(overhead)

    all_days = sorted(
        set(agent_days) | set(llm_days) | set(qual_days) | set(pipe_days) | set(refs.get("agent_latency_ms_p95") or {})
    )
    if use_reference and "2026-05-20" not in all_days:
        all_days.append("2026-05-20")
        all_days.sort()

    daily: List[Dict[str, Any]] = []
    for day in all_days:
        ad = agent_days[day]
        agent_d = ad.to_dict()
        ref_agent_p95 = (refs.get("agent_latency_ms_p95") or {}).get(day)
        if use_reference and ref_agent_p95 and not agent_d.get("latency_ms_p95"):
            agent_d["latency_ms_p95"] = ref_agent_p95
            agent_d["latency_ms_p95_source"] = "reference"

        row = {
            "day": day,
            "phase": _phase_for_date(day, phases),
            "agent": agent_d,
            "llm": llm_days[day].to_dict(refs=refs, day=day, use_reference=use_reference),
            "pipeline": pipe_days[day].to_dict(),
            "quality": qual_days[day].to_dict(refs=refs, day=day, use_reference=use_reference),
        }
        daily.append(row)

    phase_compare: List[Dict[str, Any]] = []
    by_phase: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in daily:
        by_phase[row["phase"]].append(row)

    for pid, start, end, label in phases:
        rows = by_phase.get(pid, [])
        if not rows:
            continue
        phase_compare.append(
            {
                "id": pid,
                "label": label,
                "start": start,
                "end": end,
                "agent": _aggregate_phase(rows, "agent"),
                "llm": _aggregate_phase(rows, "llm"),
                "pipeline": _aggregate_phase(rows, "pipeline"),
                "quality": _aggregate_phase(rows, "quality"),
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "registry_path": str(_REGISTRY_PATH),
        "llm_records": len(llm_rows),
        "ops_trace_path": str(ops_path),
        "layers": {
            "agent": {"daily": [r["agent"] for r in daily], "description": "orchestrator + модули до ответа"},
            "llm": {"daily": [r["llm"] for r in daily], "description": "OpenRouter вызовы"},
            "pipeline": {"daily": [r["pipeline"] for r in daily], "description": "доля LLM в полном ходе"},
            "quality": {"daily": [r["quality"] for r in daily], "description": "CDC и feedback"},
        },
        "daily": daily,
        "phases": phase_compare,
        "phase_definitions": [{"id": a, "start": b, "end": c, "label": d} for a, b, c, d in phases],
    }


def _render_markdown(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# Метрики по периодам: агент vs LLM\n")
    lines.append(f"- Сгенерировано: `{report['generated_at']}`")
    lines.append(f"- Schema: **v{report.get('schema_version')}** · реестр: `config/metrics_period_registry.json`")
    lines.append(f"- LLM записей: **{report.get('llm_records')}** · ops_trace: `{report.get('ops_trace_path')}`\n")

    lines.append("## Сравнение фаз\n")
    lines.append(
        "| Фаза | Ходов агент | p95 агент (ms) | LLM calls | p95 LLM (ms) | Доля LLM % | overhead med | brain_first tok | CDC ok % |"
    )
    lines.append("|------|-------------|----------------|-----------|--------------|------------|--------------|-----------------|----------|")
    for p in report.get("phases") or []:
        ag = p.get("agent") or {}
        lm = p.get("llm") or {}
        pl = p.get("pipeline") or {}
        qu = p.get("quality") or {}
        lines.append(
            f"| {p.get('label')} | {ag.get('turns_n') or '—'} | "
            f"{ag.get('latency_ms_p95') or '—'} | {lm.get('calls_n') or '—'} | "
            f"{lm.get('latency_ms_p95') or '—'} | {pl.get('llm_share_of_turn_pct') or '—'} | "
            f"{pl.get('overhead_ms_median') or '—'} | {lm.get('brain_first_median_prompt') or '—'} | "
            f"{qu.get('cdc_ok_pct') or '—'} |"
        )

    lines.append("\n## По дням: скорость\n")
    lines.append(
        "| День | Агент med ms | Агент p95 | LLM med | LLM p95 | LLM доля % | overhead | fast_path % | math % |"
    )
    lines.append("|------|--------------|-----------|---------|---------|------------|----------|-------------|--------|")
    for r in report.get("daily") or []:
        ag = r.get("agent") or {}
        lm = r.get("llm") or {}
        pl = r.get("pipeline") or {}
        lines.append(
            f"| {r['day']} | {ag.get('latency_ms_median') or '—'} | {ag.get('latency_ms_p95') or '—'} | "
            f"{lm.get('latency_ms_median') or '—'} | {lm.get('latency_ms_p95') or '—'} | "
            f"{pl.get('llm_share_of_turn_pct') or '—'} | {pl.get('overhead_ms_median') or '—'} | "
            f"{ag.get('fast_path_pct') or '—'} | {ag.get('math_module_pct') or '—'} |"
        )

    lines.append("\n## LLM по тегам (последний день с данными)\n")
    last_llm = None
    last_day = ""
    for r in reversed(report.get("daily") or []):
        bt = (r.get("llm") or {}).get("by_tag")
        if bt:
            last_llm = bt
            last_day = r["day"]
            break
    if last_llm:
        lines.append(f"День `{last_day}`:\n")
        for tag, st in sorted(last_llm.items()):
            lines.append(
                f"- `{tag}`: n={st.get('n')}, med={st.get('latency_ms_median')} ms, "
                f"p95={st.get('latency_ms_p95')} ms"
            )
    else:
        lines.append("— нет данных\n")

    lines.append("\n## Накопление истории\n")
    lines.append(
        "Каждый запуск с `--history data/benchmarks/metrics_snapshots.jsonl` дописывает снимок. "
        "Сравнение прогонов: `python scripts/metrics_period_compare.py` (если добавите позже) или jq по JSONL.\n"
    )
    lines.append(
        "```bash\n"
        "python scripts/metrics_period_report.py --root . \\\n"
        "  --json data/benchmarks/metrics_periods_latest.json \\\n"
        "  --history data/benchmarks/metrics_snapshots.jsonl \\\n"
        "  --out docs/METRICS_PERIODS_RU.md\n"
        "```\n"
    )
    return "\n".join(lines) + "\n"


def _append_history(path: Path, report: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    snap = {
        "snapshot_id": report["generated_at"],
        "schema_version": report.get("schema_version"),
        "root": report.get("root"),
        "summary": {
            "llm_records": report.get("llm_records"),
            "days": len(report.get("daily") or []),
            "phases": len(report.get("phases") or []),
        },
        "report": report,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(snap, ensure_ascii=False) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="Метрики по периодам: агент + LLM + история")
    ap.add_argument("--root", default=".", help="Корень gemma_bot")
    ap.add_argument("--out", default="", help="Markdown отчёт")
    ap.add_argument("--json", default="", help="Последний снимок JSON")
    ap.add_argument(
        "--history",
        default="",
        help="JSONL для накопления снимков (дописывается при каждом запуске)",
    )
    ap.add_argument("--no-reference", action="store_true", help="Без серверных эталонов 15–20 мая")
    ap.add_argument("--no-history", action="store_true", help="Не писать в --history")
    args = ap.parse_args()
    root = Path(args.root).resolve()

    report = collect(root, use_reference=not args.no_reference)

    json_path = args.json or "data/benchmarks/metrics_periods_latest.json"
    out_j = Path(json_path)
    if not out_j.is_absolute():
        out_j = root / out_j
    out_j.parent.mkdir(parents=True, exist_ok=True)
    out_j.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Wrote", out_j)

    history_path = args.history or "data/benchmarks/metrics_snapshots.jsonl"
    if not args.no_history:
        hist = Path(history_path)
        if not hist.is_absolute():
            hist = root / hist
        _append_history(hist, report)
        print("Appended history", hist)

    md = _render_markdown(report)
    out_md = args.out or "docs/METRICS_PERIODS_RU.md"
    out_m = Path(out_md)
    if not out_m.is_absolute():
        out_m = root / out_m
    out_m.parent.mkdir(parents=True, exist_ok=True)
    out_m.write_text(md, encoding="utf-8")
    print("Wrote", out_m)

    if not args.out and not args.json:
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
