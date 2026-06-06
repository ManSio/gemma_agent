from __future__ import annotations

import logging
import os
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple

from core.number_parse import parse_env_float


logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    return parse_env_float(name, default)


@dataclass
class TraceContext:
    trace_id: str
    started_at: float
    stage: str = "input"
    marks: List[Tuple[str, float]] = field(default_factory=list)


class Observability:
    def __init__(self) -> None:
        self.counters: Dict[str, int] = defaultdict(int)
        self.latencies_ms: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=500))
        self.active_traces: Dict[str, TraceContext] = {}

    def new_trace(self) -> TraceContext:
        tid = uuid.uuid4().hex
        ctx = TraceContext(trace_id=tid, started_at=time.perf_counter())
        self.active_traces[tid] = ctx
        self.inc("traces_started")
        return ctx

    def stage(self, trace_id: str, stage: str) -> None:
        if trace_id in self.active_traces:
            self.active_traces[trace_id].stage = stage

    def mark(self, trace_id: str, name: str) -> None:
        """Точка на шкале времени внутри trace (perf_counter)."""
        ctx = self.active_traces.get(trace_id)
        if not ctx:
            return
        ctx.marks.append((name, time.perf_counter()))

    def finish(self, trace_id: str, label: str = "pipeline") -> Optional[float]:
        ctx = self.active_traces.pop(trace_id, None)
        if not ctx:
            return None
        now = time.perf_counter()
        elapsed_ms = (now - ctx.started_at) * 1000.0
        self.observe_latency(label, elapsed_ms)
        self.inc("traces_finished")
        self._log_latency_breakdown(trace_id, ctx, elapsed_ms, now)
        return elapsed_ms

    def _log_latency_breakdown(
        self,
        trace_id: str,
        ctx: TraceContext,
        elapsed_ms: float,
        now: float,
    ) -> None:
        if not ctx.marks:
            return
        policy = (os.getenv("LATENCY_TRACE_LOG") or "slow").strip().lower()
        slow_ms = max(100.0, min(_env_float("LATENCY_TRACE_SLOW_MS", 2500.0), 120000.0))
        prev_t = ctx.started_at
        parts: List[str] = []
        max_seg = 0.0
        for name, t in ctx.marks:
            seg_ms = (t - prev_t) * 1000.0
            max_seg = max(max_seg, seg_ms)
            parts.append(f"{name}={seg_ms:.0f}ms")
            prev_t = t
        tail_ms = (now - prev_t) * 1000.0
        max_seg = max(max_seg, tail_ms)
        parts.append(f"tail={tail_ms:.0f}ms")
        line = " │ ".join(parts)
        if policy in ("1", "true", "yes", "on", "all", "always"):
            lvl = logging.INFO
        elif policy in ("0", "false", "off", "no", "never"):
            lvl = logging.DEBUG
        else:
            # slow: INFO если общий порог или любой сегмент > 1s
            lvl = logging.INFO if (elapsed_ms >= slow_ms or max_seg >= 1000.0) else logging.DEBUG
        logger.log(
            lvl,
            "latency trace=%s total=%.0fms │ %s",
            trace_id[:12],
            elapsed_ms,
            line,
        )

    def inc(self, key: str, delta: int = 1) -> None:
        self.counters[key] += delta

    def observe_latency(self, key: str, value_ms: float) -> None:
        self.latencies_ms[key].append(float(value_ms))

    def p95(self, key: str) -> float:
        vals = list(self.latencies_ms.get(key, []))
        if not vals:
            return 0.0
        vals.sort()
        idx = int(0.95 * (len(vals) - 1))
        return float(vals[idx])

    def snapshot(self) -> Dict[str, object]:
        return {
            "counters": dict(self.counters),
            "latency_p95_ms": {k: self.p95(k) for k in self.latencies_ms.keys()},
            "active_traces": len(self.active_traces),
        }

    def stage_ms_snapshot(self, trace_id: str) -> Optional[Dict[str, int]]:
        """Сегменты OBS.mark → ms для turns.jsonl (до finish trace)."""
        ctx = self.active_traces.get(str(trace_id or "").strip())
        if not ctx:
            return None
        now = time.perf_counter()
        prev = ctx.started_at
        out: Dict[str, int] = {}
        for name, t in ctx.marks:
            key = str(name or "mark").strip() or "mark"
            out[key] = max(0, int((t - prev) * 1000.0))
            prev = t
        out["tail"] = max(0, int((now - prev) * 1000.0))
        out["total"] = max(0, int((now - ctx.started_at) * 1000.0))
        return out


OBS = Observability()
