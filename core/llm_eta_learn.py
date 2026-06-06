"""
Накопление фактических задержек LLM для более точного ETC в Telegram.

Пишет агрегаты в JSON (EMA по корзинам: latency, completion tok/s, assembly).
Запись — после успешных ответов tiered-вызова и при первом LLM-этапе (assembly).
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_FILE_VER = 2


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _stats_path() -> Path:
    raw = (os.getenv("GEMMA_LLM_ETA_STATS_PATH") or "").strip()
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[1] / "data" / "runtime" / "llm_eta_stats.json"


def _bucket_stage_from_tag(tag: str) -> str:
    t = (tag or "").lower()
    if "first_stage" in t:
        return "first"
    if "second_stage" in t:
        return "second"
    if "tool_chain" in t:
        return "tool_chain"
    return "other"


def _bucket_tier(task_tier: Optional[str]) -> str:
    x = (task_tier or "").strip().lower()
    if x == "deep":
        return "deep"
    if x == "nested":
        return "nested"
    return "std"


def _bucket_max_tok_bin(max_tokens: int) -> str:
    m = max(0, int(max_tokens or 0))
    if m <= 800:
        return "t8"
    if m <= 1600:
        return "t16"
    if m <= 3200:
        return "t32"
    return "t32p"


def bucket_key(*, tag: str, task_tier: Optional[str], max_tokens: int) -> str:
    return f"{_bucket_stage_from_tag(tag)}|{_bucket_tier(task_tier)}|{_bucket_max_tok_bin(max_tokens)}"


def _normalize_stage(stage: str) -> str:
    s = (stage or "").strip().lower()
    if s in {"first", "second", "tool_chain", "other"}:
        return s
    if s in {"1", "first_stage"}:
        return "first"
    if s in {"2", "second_stage"}:
        return "second"
    if "tool" in s and "chain" in s:
        return "tool_chain"
    return "other"


def _completion_tokens(res: Dict[str, Any]) -> int:
    ud = res.get("usage_detail")
    if isinstance(ud, dict):
        try:
            ct = int(ud.get("completion_tokens") or 0)
            if ct > 0:
                return ct
        except (TypeError, ValueError):
            pass
    try:
        return max(0, int(res.get("completion_tokens") or 0))
    except (TypeError, ValueError):
        return 0


def _ema_update(prev: float, sample: float, n: int, alpha: float) -> float:
    if n <= 1 or prev <= 0:
        return sample
    return alpha * sample + (1.0 - alpha) * prev


def _load() -> Dict[str, Any]:
    p = _stats_path()
    if not p.is_file():
        return {"version": _FILE_VER, "buckets": {}, "assembly": {}}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {"version": _FILE_VER, "buckets": {}, "assembly": {}}
        b = raw.get("buckets")
        if not isinstance(b, dict):
            b = {}
        asm = raw.get("assembly")
        if not isinstance(asm, dict):
            asm = {}
        return {"version": int(raw.get("version") or _FILE_VER), "buckets": dict(b), "assembly": dict(asm)}
    except Exception as e:
        logger.warning("llm_eta_learn load failed: %s", e)
        return {"version": _FILE_VER, "buckets": {}, "assembly": {}}


def _save(data: Dict[str, Any]) -> None:
    p = _stats_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    data["version"] = _FILE_VER
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def learn_from_llm_result(
    res: Dict[str, Any],
    *,
    tag: str,
    task_tier: Optional[str],
    max_tokens: int,
    prompt: str,
) -> None:
    if not _truthy("BRAIN_LLM_ETA_LEARN_ENABLED", True):
        return
    if not res or res.get("error"):
        return
    if not str(res.get("content") or "").strip():
        return
    lat = res.get("latency_ms")
    if lat is None:
        return
    try:
        sec = float(lat) / 1000.0
    except (TypeError, ValueError):
        return
    lo = _f("BRAIN_LLM_ETA_LEARN_LAT_MIN_SEC", 0.35)
    hi = _f("BRAIN_LLM_ETA_LEARN_LAT_MAX_SEC", 720.0)
    if not (lo <= sec <= hi):
        return

    key = bucket_key(tag=tag, task_tier=task_tier, max_tokens=max_tokens)
    alpha = _f("BRAIN_LLM_ETA_LEARN_EMA_ALPHA", 0.12)
    alpha = max(0.02, min(alpha, 0.6))
    ct = _completion_tokens(res)

    with _lock:
        data = _load()
        buckets: Dict[str, Any] = data.setdefault("buckets", {})
        row = buckets.get(key)
        if not isinstance(row, dict):
            row = {}
        n = int(row.get("n") or 0) + 1
        prev_ema = float(row.get("ema_sec") or 0.0)
        ema = _ema_update(prev_ema, sec, n, alpha)
        prev_ct = float(row.get("ema_completion_tokens") or 0.0)
        ema_ct = prev_ct
        if ct > 0:
            ema_ct = _ema_update(prev_ct, float(ct), n, alpha)
        prev_tps = float(row.get("ema_tps") or 0.0)
        ema_tps = prev_tps
        if ct > 0 and sec > 0.05:
            sample_tps = float(ct) / sec
            lo_tps = _f("BRAIN_LLM_ETA_LEARN_TPS_MIN", 4.0)
            hi_tps = _f("BRAIN_LLM_ETA_LEARN_TPS_MAX", 400.0)
            if lo_tps <= sample_tps <= hi_tps:
                ema_tps = _ema_update(prev_tps, sample_tps, n, alpha)
        buckets[key] = {
            "n": n,
            "ema_sec": round(ema, 3),
            "last_sec": round(sec, 3),
            "ema_completion_tokens": round(ema_ct, 1),
            "ema_tps": round(ema_tps, 2),
            "max_tokens": int(max_tokens),
            "prompt_chars": len(prompt or ""),
        }
        try:
            _save(data)
        except Exception as e:
            logger.warning("llm_eta_learn save failed: %s", e)


def learn_assembly_sec(sec: float) -> None:
    """Время от arm прогресса до первого LLM-этапа (сборка промпта, prefetch)."""
    if not _truthy("BRAIN_LLM_ETA_ASSEMBLY_LEARN_ENABLED", True):
        return
    if not _truthy("BRAIN_LLM_ETA_LEARN_ENABLED", True):
        return
    try:
        sample = float(sec)
    except (TypeError, ValueError):
        return
    lo = _f("BRAIN_LLM_ETA_ASSEMBLY_LEARN_MIN_SEC", 0.5)
    hi = _f("BRAIN_LLM_ETA_ASSEMBLY_LEARN_MAX_SEC", 120.0)
    if not (lo <= sample <= hi):
        return
    alpha = _f("BRAIN_LLM_ETA_LEARN_EMA_ALPHA", 0.12)
    alpha = max(0.02, min(alpha, 0.6))
    with _lock:
        data = _load()
        asm = data.setdefault("assembly", {})
        if not isinstance(asm, dict):
            asm = {}
            data["assembly"] = asm
        n = int(asm.get("n") or 0) + 1
        prev = float(asm.get("ema_sec") or 0.0)
        ema = _ema_update(prev, sample, n, alpha)
        data["assembly"] = {"n": n, "ema_sec": round(ema, 3), "last_sec": round(sample, 3)}
        try:
            _save(data)
        except Exception as e:
            logger.warning("llm_eta_learn assembly save failed: %s", e)


def blended_assembly_sec() -> Optional[float]:
    if not _truthy("BRAIN_LLM_ETA_ASSEMBLY_LEARN_ENABLED", True):
        return None
    if not _truthy("BRAIN_LLM_ETA_LEARN_ENABLED", True):
        return None
    min_n = _i("BRAIN_LLM_ETA_ASSEMBLY_LEARN_MIN_SAMPLES", 3)
    with _lock:
        data = _load()
        asm = data.get("assembly")
        if not isinstance(asm, dict):
            return None
        n = int(asm.get("n") or 0)
        ema = float(asm.get("ema_sec") or 0.0)
        if n < min_n or ema <= 0:
            return None
        cap = _f("BRAIN_LLM_ETA_ASSEMBLY_LEARN_MAX_SEC", 45.0)
        return min(ema, cap)


def _lookup_raw(stage: str, task_tier: str, max_tokens: int) -> Tuple[Optional[float], int, Optional[float], Optional[float]]:
    st = _normalize_stage(stage)
    tier = _bucket_tier(task_tier)
    tok_bin = _bucket_max_tok_bin(max_tokens)
    keys = (
        f"{st}|{tier}|{tok_bin}",
        f"{st}|std|{tok_bin}",
        f"{st}|{tier}|t16",
        f"{st}|std|t16",
    )
    with _lock:
        data = _load()
        buckets = data.get("buckets") if isinstance(data.get("buckets"), dict) else {}
        for k in keys:
            row = buckets.get(k)
            if isinstance(row, dict):
                n = int(row.get("n") or 0)
                ema = float(row.get("ema_sec") or 0.0)
                ema_ct = float(row.get("ema_completion_tokens") or 0.0)
                ema_tps = float(row.get("ema_tps") or 0.0)
                if n > 0 and ema > 0:
                    return (
                        ema,
                        n,
                        ema_ct if ema_ct > 0 else None,
                        ema_tps if ema_tps > 0 else None,
                    )
    return None, 0, None, None


def lookup_learned_tps(*, stage: str, task_tier: str, max_tokens: int) -> Optional[float]:
    if not _truthy("BRAIN_LLM_ETA_LEARN_ENABLED", True):
        return None
    _, n, _, ema_tps = _lookup_raw(stage, task_tier, max_tokens)
    min_n = _i("BRAIN_LLM_ETA_LEARN_MIN_SAMPLES", 4)
    if ema_tps is None or n < min_n:
        return None
    lo = _f("BRAIN_LLM_ETA_LEARN_TPS_MIN", 4.0)
    hi = _f("BRAIN_LLM_ETA_LEARN_TPS_MAX", 400.0)
    return max(lo, min(ema_tps, hi))


def lookup_learned_completion(*, stage: str, task_tier: str, max_tokens: int) -> Optional[float]:
    if not _truthy("BRAIN_LLM_ETA_LEARN_ENABLED", True):
        return None
    _, n, ema_ct, _ = _lookup_raw(stage, task_tier, max_tokens)
    min_n = _i("BRAIN_LLM_ETA_LEARN_MIN_SAMPLES", 4)
    if ema_ct is None or n < min_n:
        return None
    return max(32.0, ema_ct)


def blended_eta_sec(
    *,
    stage: str,
    task_tier: str,
    max_tokens: int,
    heuristic_sec: float,
) -> float:
    """
    Смесь EMA по истории и эвристики. Пока мало образцов — почти только эвристика.
    """
    if not _truthy("BRAIN_LLM_ETA_LEARN_ENABLED", True):
        return max(1.0, float(heuristic_sec))
    ema, n, _, _ = _lookup_raw(stage, task_tier, max_tokens)
    h = max(1.0, float(heuristic_sec))
    if ema is None or n <= 0:
        return h
    min_n = _i("BRAIN_LLM_ETA_LEARN_MIN_SAMPLES", 4)
    span = max(1, _i("BRAIN_LLM_ETA_LEARN_BLEND_SPAN", 18))
    if n < min_n:
        return h
    w = min(1.0, float(n - min_n + 1) / float(span))
    out = max(1.0, ema * w + h * (1.0 - w))
    cap_m = _f("BRAIN_LLM_ETA_LEARN_MAX_MULT", 2.75)
    cap_m = max(1.1, min(cap_m, 6.0))
    return min(out, h * cap_m)


def snapshot_for_operator() -> Dict[str, Any]:
    """Краткий снимок для /admin_operator (опционально)."""
    with _lock:
        data = _load()
    buckets = data.get("buckets") if isinstance(data.get("buckets"), dict) else {}
    asm = data.get("assembly") if isinstance(data.get("assembly"), dict) else {}
    n_b = len(buckets)
    n_s = sum(int((v or {}).get("n") or 0) for v in buckets.values() if isinstance(v, dict))
    return {
        "llm_eta_learn_enabled": _truthy("BRAIN_LLM_ETA_LEARN_ENABLED", True),
        "stats_path": str(_stats_path()),
        "buckets": n_b,
        "samples": n_s,
        "assembly_ema_sec": asm.get("ema_sec"),
        "assembly_samples": asm.get("n"),
    }
