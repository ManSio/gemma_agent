#!/usr/bin/env python3
"""
Поиск по журналам ходов (read-only) — для разбора прод-инцидентов.

    python scripts/turns_search.py погода --days 7
    python scripts/turns_search.py "район да" --days 3 --limit 20
    python scripts/turns_search.py trace_id_here --source ops
    python scripts/turns_search.py --json погода

Файлы по умолчанию:
  data/runtime/turns.jsonl
  data/runtime/ops_trace.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SEARCH_FIELDS_TURNS = (
    "user_text",
    "user_excerpt",
    "assistant_text",
    "assistant_excerpt",
    "intent",
    "trace_id",
    "user_id",
)
SEARCH_FIELDS_OPS = (
    "user_text",
    "assistant_text",
    "trace_id",
    "user_id",
    "channel",
)


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


def _haystack(row: dict, fields: Iterable[str]) -> str:
    parts: List[str] = []
    for f in fields:
        v = row.get(f)
        if v is None:
            continue
        if isinstance(v, list):
            parts.extend(str(x) for x in v)
        else:
            parts.append(str(v))
    issues = row.get("issues")
    if isinstance(issues, list):
        parts.extend(str(x) for x in issues)
    return " ".join(parts).lower()


def _match_query(hay: str, query: str) -> bool:
    q = query.strip().lower()
    if not q:
        return False
    tokens = [t for t in q.split() if t]
    if not tokens:
        return False
    return all(t in hay for t in tokens)


def _iter_jsonl_tail(path: Path, max_lines: int) -> Iterable[dict]:
    if not path.is_file():
        return
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if max_lines > 0 and len(lines) > max_lines:
        lines = lines[-max_lines:]
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            d = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict):
            yield d


def search_file(
    path: Path,
    query: str,
    *,
    since: Optional[datetime],
    fields: Iterable[str],
    limit: int,
    skip_scenario: bool,
    tail_lines: int,
) -> List[dict]:
    hits: List[dict] = []
    for row in _iter_jsonl_tail(path, tail_lines):
        if skip_scenario and row.get("type") == "scenario":
            continue
        ts = _parse_ts(row.get("ts"))
        if since and ts and ts < since:
            continue
        if not _match_query(_haystack(row, fields), query):
            continue
        hits.append(row)
    hits.sort(key=lambda r: str(r.get("ts") or ""), reverse=True)
    return hits[:limit]


def _summarize(row: dict, source: str) -> Dict[str, Any]:
    user = str(row.get("user_text") or row.get("user_excerpt") or "")[:200]
    ast = str(row.get("assistant_text") or row.get("assistant_excerpt") or "")[:200]
    return {
        "source": source,
        "ts": str(row.get("ts") or "")[:24],
        "user_id": row.get("user_id"),
        "trace_id": row.get("trace_id"),
        "intent": row.get("intent"),
        "issues": row.get("issues"),
        "ok": row.get("ok"),
        "user": user,
        "assistant": ast,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Поиск в turns.jsonl / ops_trace.jsonl")
    ap.add_argument("query", nargs="+", help="Слова для поиска (все должны встретиться)")
    ap.add_argument("--days", type=float, default=7.0, help="Окно в днях (0 = без фильтра)")
    ap.add_argument("--limit", type=int, default=30, help="Макс. совпадений всего")
    ap.add_argument(
        "--tail-lines",
        type=int,
        default=5000,
        help="Сколько последних строк jsonl читать (0 = весь файл)",
    )
    ap.add_argument(
        "--source",
        choices=("turns", "ops", "both"),
        default="both",
        help="Какой журнал искать",
    )
    ap.add_argument("--root", type=Path, default=None, help="Корень проекта")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except Exception:
        pass

    project_root = args.root or Path(os.getenv("GEMMA_PROJECT_ROOT") or ROOT)
    runtime = project_root / "data" / "runtime"
    query = " ".join(args.query)

    since: Optional[datetime] = None
    if args.days > 0:
        since = datetime.now(timezone.utc) - timedelta(days=args.days)

    all_hits: List[dict] = []
    paths: List[tuple[str, Path, Iterable[str], bool]] = []
    if args.source in ("turns", "both"):
        paths.append(("turns", runtime / "turns.jsonl", SEARCH_FIELDS_TURNS, True))
    if args.source in ("ops", "both"):
        paths.append(("ops", runtime / "ops_trace.jsonl", SEARCH_FIELDS_OPS, False))

    for name, path, fields, skip_scenario in paths:
        if not path.is_file():
            if args.as_json:
                print(json.dumps({"warning": f"нет файла {path}"}, ensure_ascii=False), file=sys.stderr)
            else:
                print(f"# нет файла {path}", file=sys.stderr)
            continue
        rows = search_file(
            path,
            query,
            since=since,
            fields=fields,
            limit=args.limit,
            skip_scenario=skip_scenario,
            tail_lines=args.tail_lines,
        )
        for row in rows:
            all_hits.append(_summarize(row, name))

    all_hits.sort(key=lambda x: x.get("ts") or "", reverse=True)
    if len(all_hits) > args.limit:
        all_hits = all_hits[: args.limit]

    if args.as_json:
        print(
            json.dumps(
                {"query": query, "days": args.days, "count": len(all_hits), "hits": all_hits},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"Запрос: {query!r}  окно: {args.days} дн.  найдено: {len(all_hits)}")
        for i, h in enumerate(all_hits, 1):
            print(f"\n--- {i} [{h['source']}] {h['ts']} user={h.get('user_id')} trace={h.get('trace_id')}")
            if h.get("intent"):
                print(f"intent: {h['intent']}")
            if h.get("issues"):
                print(f"issues: {h['issues']}")
            if h.get("user"):
                print(f"user: {h['user']}")
            if h.get("assistant"):
                print(f"bot:  {h['assistant']}")

    return 0 if all_hits else 1


if __name__ == "__main__":
    raise SystemExit(main())
