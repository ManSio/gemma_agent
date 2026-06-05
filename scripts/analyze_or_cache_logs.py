#!/usr/bin/env python3
"""Анализ llm_cache_telemetry из panel_nohup_bot.log* на сервере."""
from __future__ import annotations

import glob
import json
import os
import re
from collections import defaultdict

LOG_DIR = os.environ.get("GEMMA_ROOT", "/opt/gemma_agent")
METRICS = os.path.join(LOG_DIR, "data/runtime/metrics_timeseries.jsonl")

pat_tel = re.compile(
    r"llm_cache_telemetry session=([^ ]+) model=([^ ]+) "
    r"prompt_tok=(\d+) cached_tok=(\d+) cache_write=(\d+) reasoning=(\d+) latency_ms=(\d+)"
)
pat_audit = re.compile(
    r"openrouter ok latency_ms=(\d+) model=([^ ]+) upstream=([^ ]+) "
    r"tokens_total=(\d+) cost=([\d.eE+-]+) chars=(\d+) cached_tok=(\d+) cache_write=(\d+)"
)


def load_rows() -> list[dict]:
    rows: list[dict] = []
    for fp in sorted(glob.glob(os.path.join(LOG_DIR, "panel_nohup_bot.log*"))):
        with open(fp, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                m = pat_tel.search(line)
                if m:
                    rows.append(
                        {
                            "kind": "telemetry",
                            "session": m.group(1),
                            "model": m.group(2),
                            "prompt": int(m.group(3)),
                            "cached": int(m.group(4)),
                            "cache_write": int(m.group(5)),
                            "reasoning": int(m.group(6)),
                            "latency": int(m.group(7)),
                        }
                    )
                    continue
                m = pat_audit.search(line)
                if m:
                    rows.append(
                        {
                            "kind": "audit",
                            "session": "?",
                            "model": m.group(2),
                            "upstream": m.group(3),
                            "cached": int(m.group(7)),
                            "cache_write": int(m.group(8)),
                            "latency": int(m.group(1)),
                            "tokens_total": int(m.group(4)),
                        }
                    )
    return rows


def main() -> None:
    host = os.environ.get("HOSTNAME", "?")
    print("HOST", host)
    print("GEMMA_ROOT", LOG_DIR)
    rows = load_rows()
    tel = [r for r in rows if r["kind"] == "telemetry"]
    print("TELEMETRY_ROWS", len(tel), "AUDIT_ROWS", len(rows) - len(tel))
    if not tel:
        return

    hits = [r for r in tel if r["cached"] > 0]
    cached_sum = sum(r["cached"] for r in tel)
    prompt_sum = sum(r["prompt"] for r in tel)
    print(
        "CALLS",
        len(tel),
        "WITH_CACHE",
        len(hits),
        "({:.1f}%)".format(100 * len(hits) / len(tel)),
    )
    print(
        "SUM cached/prompt",
        cached_sum,
        "/",
        prompt_sum,
        "({:.1f}%)".format(100 * cached_sum / prompt_sum if prompt_sum else 0),
    )

    lat_hit = [r["latency"] for r in hits]
    lat_miss = [r["latency"] for r in tel if r["cached"] == 0]
    print(
        "LATENCY ms all_avg={:.0f} hit_avg={:.0f} miss_avg={:.0f}".format(
            sum(r["latency"] for r in tel) / len(tel),
            sum(lat_hit) / len(lat_hit) if lat_hit else 0,
            sum(lat_miss) / len(lat_miss) if lat_miss else 0,
        )
    )

    by_sess: dict = defaultdict(lambda: {"n": 0, "hit": 0, "cached": 0, "prompt": 0, "lat": []})
    for r in tel:
        d = by_sess[r["session"]]
        d["n"] += 1
        d["prompt"] += r["prompt"]
        d["cached"] += r["cached"]
        d["lat"].append(r["latency"])
        if r["cached"] > 0:
            d["hit"] += 1

    print("\n=== BY SESSION ===")
    for s, d in sorted(by_sess.items(), key=lambda x: -x[1]["n"])[:15]:
        pct = 100 * d["cached"] / d["prompt"] if d["prompt"] else 0
        hr = 100 * d["hit"] / d["n"] if d["n"] else 0
        avg_lat = sum(d["lat"]) / len(d["lat"]) if d["lat"] else 0
        print(
            "  {} | n={} hit={} ({:.0f}%) cached/prompt={:.0f}% avg_lat={:.0f}ms".format(
                s, d["n"], d["hit"], hr, pct, avg_lat
            )
        )

    by_model: dict = defaultdict(lambda: {"n": 0, "hit": 0, "cached": 0, "prompt": 0, "lat": []})
    for r in tel:
        d = by_model[r["model"]]
        d["n"] += 1
        d["prompt"] += r["prompt"]
        d["cached"] += r["cached"]
        d["lat"].append(r["latency"])
        if r["cached"] > 0:
            d["hit"] += 1

    print("\n=== BY MODEL ===")
    for m, d in sorted(by_model.items(), key=lambda x: -x[1]["n"]):
        pct = 100 * d["cached"] / d["prompt"] if d["prompt"] else 0
        hr = 100 * d["hit"] / d["n"] if d["n"] else 0
        avg_lat = sum(d["lat"]) / len(d["lat"]) if d["lat"] else 0
        print(
            "  {} | n={} hit={} ({:.0f}%) cached/prompt={:.0f}% avg_lat={:.0f}ms".format(
                m, d["n"], d["hit"], hr, pct, avg_lat
            )
        )

    no_sess = [r for r in tel if r["session"] == "-"]
    with_sess = [r for r in tel if r["session"] != "-"]
    print("\n=== SESSION HEADER ===")
    if with_sess:
        print(
            "  X-Session-Id set: n={} hit_rate={:.0f}%".format(
                len(with_sess),
                100 * sum(1 for r in with_sess if r["cached"] > 0) / len(with_sess),
            )
        )
    if no_sess:
        print(
            "  session=- : n={} hit_rate={:.0f}%".format(
                len(no_sess),
                100 * sum(1 for r in no_sess if r["cached"] > 0) / len(no_sess),
            )
        )

    # epoch/profile pattern
    by_prof: dict = defaultdict(lambda: {"n": 0, "hit": 0, "cached": 0, "prompt": 0})
    for r in tel:
        s = r["session"]
        if s == "-":
            prof = "(no_session)"
        elif ".e" in s:
            prof = s.split(".", 1)[-1] if "." in s else s
        else:
            prof = s
        d = by_prof[prof]
        d["n"] += 1
        d["prompt"] += r["prompt"]
        d["cached"] += r["cached"]
        if r["cached"] > 0 and r["cached"] > 0:
            d["hit"] += 1

    print("\n=== BY PROFILE SUFFIX ===")
    for p, d in sorted(by_prof.items(), key=lambda x: -x[1]["n"])[:12]:
        pct = 100 * d["cached"] / d["prompt"] if d["prompt"] else 0
        hr = 100 * d["hit"] / d["n"] if d["n"] else 0
        print("  {} | n={} hit={} ({:.0f}%) cached/prompt={:.0f}%".format(p, d["n"], d["hit"], hr, pct))

    print("\n=== TOP CACHE HITS ===")
    for r in sorted(hits, key=lambda x: -x["cached"])[:10]:
        print(
            "  cached={} prompt={} lat={}ms session={} model={}".format(
                r["cached"], r["prompt"], r["latency"], r["session"], r["model"]
            )
        )

    if os.path.isfile(METRICS):
        last = None
        with open(METRICS, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.strip():
                    last = line
        if last:
            c = json.loads(last).get("counters") or {}
            print("\n=== MONITOR COUNTERS ===")
            for k in sorted(c):
                if "reuse" in k or "openrouter_completion" in k or "openrouter_prompt" in k:
                    print("  {}={}".format(k, c[k]))

    env_keys = [
        "OPENROUTER_PROVIDER_ORDER",
        "OPENROUTER_PROVIDER_QUANTIZATIONS",
        "OPENROUTER_PROVIDER_IGNORE",
        "OPENROUTER_PROMPT_CACHE_MODE",
        "OPENROUTER_SESSION_HEADERS_ENABLED",
        "BRAIN_KV_PROFILE_STICKY",
    ]
    env_path = os.path.join(LOG_DIR, ".env")
    print("\n=== ENV (cache/provider) ===")
    if os.path.isfile(env_path):
        vals = {}
        with open(env_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k in env_keys:
                    vals[k] = v
        for k in env_keys:
            print("  {}={}".format(k, vals.get(k, "(default/not set)")))


if __name__ == "__main__":
    main()
