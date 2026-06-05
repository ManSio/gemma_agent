#!/usr/bin/env python3
"""
Бенчмарк новостного дайджеста: скорость SearX-gather + качество (validators).

По умолчанию mock (без сети). --compare: early_stop off vs on.

  python scripts/benchmark_news_digest.py --compare
  python scripts/benchmark_news_digest.py --json-out data/benchmarks/news_bench.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _fixture_search_results(n: int = 2) -> List[Dict[str, Any]]:
    return [
        {
            "title": f"Мировая новость {i + 1}: переговоры и экономика",
            "snippet": "Краткое описание события для дайджеста.",
            "url": f"https://news.example.com/world/story-{i + 1}",
        }
        for i in range(n)
    ]


def _mock_narrative_body() -> str:
    return (
        "Сегодня в центре внимания — международные переговоры и ситуация на фронте.\n\n"
        "Второй блок: экономика и энергетика в регионе.\n\n"
        "Третий блок: космическая отрасль и спортивные итоги недели."
    )


async def _bench_gather(
    *,
    early_stop: bool,
    search_delay_ms: float,
) -> Dict[str, Any]:
    from core.news_reply import _gather_digest_search_rows, news_digest_search_queries

    user_text = "какие новости в мире"
    queries = news_digest_search_queries(user_text, country="", world_feed=True)
    call_n = 0

    async def _fake_search_pack(*_a, **_k):
        nonlocal call_n
        call_n += 1
        if search_delay_ms > 0:
            await asyncio.sleep(search_delay_ms / 1000.0)
        return {"ok": True, "results": _fixture_search_results(2)}

    env = {"NEWS_DIGEST_GATHER_EARLY_STOP": "true" if early_stop else "false"}
    t0 = time.perf_counter()
    with patch.dict(os.environ, env, clear=False):
        with patch("core.news_reply._search_pack", AsyncMock(side_effect=_fake_search_pack)):
            rows = await _gather_digest_search_rows(
                queries,
                country="",
                user_id="bench",
                world_feed=True,
                user_query=user_text,
            )
    ms = (time.perf_counter() - t0) * 1000.0
    return {
        "early_stop": early_stop,
        "queries_planned": len(queries),
        "search_calls": call_n,
        "gather_ms": round(ms, 1),
        "raw_rows": len(rows),
    }


async def _bench_compose_quality() -> Dict[str, Any]:
    from core.news_reply import compose_news_digest_from_search
    from core.reform_probe_support import validate_news_world_reply

    user_text = "какие новости в мире"
    narrative = _mock_narrative_body()

    async def _fake_search_pack(*_a, **_k):
        return {"ok": True, "results": _fixture_search_results(3)}

    async def _fake_narrative(*_a, **_k):
        return narrative

    persisted: Dict[str, Any] = {"dialogue_state": {}}
    t0 = time.perf_counter()
    with patch.dict(os.environ, {"NEWS_DIGEST_GATHER_EARLY_STOP": "true"}, clear=False):
        with patch("core.news_reply._search_pack", AsyncMock(side_effect=_fake_search_pack)):
            with patch(
                "core.news_reply._llm_digest_narrative_brief",
                AsyncMock(side_effect=_fake_narrative),
            ):
                reply = await compose_news_digest_from_search(
                    user_text,
                    search_results=[],
                    persisted=persisted,
                    user_id="bench",
                )
    ms = (time.perf_counter() - t0) * 1000.0
    errs = validate_news_world_reply(str(reply or "")) if reply else ["empty_reply"]
    return {
        "compose_ms": round(ms, 1),
        "reply_len": len(str(reply or "")),
        "quality_ok": len(errs) == 0,
        "quality_errors": errs,
    }


def _render(gather_rows: List[Dict[str, Any]], quality: Dict[str, Any]) -> str:
    lines = ["=== News benchmark (mock SearX delay) ===", ""]
    for g in gather_rows:
        tag = "early_stop=ON" if g.get("early_stop") else "early_stop=OFF"
        lines.append(f"[gather {tag}]")
        lines.append(
            f"  planned queries: {g.get('queries_planned')}  actual search calls: {g.get('search_calls')}  "
            f"time: {g.get('gather_ms')} ms  raw rows: {g.get('raw_rows')}"
        )
    if len(gather_rows) == 2:
        off, on = gather_rows[0], gather_rows[1]
        lines.append(
            f"\nDelta calls: {int(off.get('search_calls', 0)) - int(on.get('search_calls', 0))}  "
            f"Delta time: {float(off.get('gather_ms', 0)) - float(on.get('gather_ms', 0)):.0f} ms"
        )
    lines.append("\n[compose + quality] early_stop=ON")
    lines.append(
        f"  time: {quality.get('compose_ms')} ms  len: {quality.get('reply_len')}  "
        f"quality: {'OK' if quality.get('quality_ok') else 'FAIL'}  errs={quality.get('quality_errors')}"
    )
    return "\n".join(lines)


async def _async_main(args: argparse.Namespace) -> int:
    delay = float(args.search_delay_ms)
    gather_rows: List[Dict[str, Any]] = []
    if args.compare:
        gather_rows.append(await _bench_gather(early_stop=False, search_delay_ms=delay))
        gather_rows.append(await _bench_gather(early_stop=True, search_delay_ms=delay))
    else:
        gather_rows.append(
            await _bench_gather(
                early_stop=_env_truthy_default(),
                search_delay_ms=delay,
            )
        )
    quality = await _bench_compose_quality()
    print(_render(gather_rows, quality))
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {"gather": gather_rows, "compose_quality": quality, "search_delay_ms": delay},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"Wrote {out}")
    return 0


def _env_truthy_default() -> bool:
    raw = (os.getenv("NEWS_DIGEST_GATHER_EARLY_STOP") or "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--search-delay-ms", type=float, default=50.0)
    ap.add_argument("--compare", action="store_true", help="off vs on early_stop")
    ap.add_argument("--json-out", default="")
    return asyncio.run(_async_main(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
