#!/usr/bin/env python3
"""
Сопоставление архивов сообщений (message_archive) с runtime-логами.

  python scripts/correlate_archives_logs.py --root /opt/gemma_agent
  python scripts/correlate_archives_logs.py --root . --out docs/archive/TURN_RECONSTRUCTION_RU.md

Ключ связи: fingerprint(user_text) + user_id + окно времени (telegram_ts / ts).
"""
from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import hashlib
import re as _re

_WS_RE = _re.compile(r"\s+")


def normalize_user_text(text: str) -> str:
    s = (text or "").strip().lower()
    return _WS_RE.sub(" ", s)


def fingerprint(text: str) -> str:
    norm = normalize_user_text(text)
    if not norm:
        return ""
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def _parse_ts(s: str) -> Optional[float]:
    if not s:
        return None
    s = str(s).strip()
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
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


def _archive_dir(root: Path) -> Path:
    for rel in (
        "data/users/behavior/message_archive",
        "data/behavior/message_archive",
    ):
        p = root / rel
        if p.is_dir() and any(p.glob("*.json")):
            return p
    return root / "data/users/behavior/message_archive"


def _load_archives(arch_dir: Path) -> Dict[str, List[Dict[str, Any]]]:
    """user_id -> list of turns {role, text, ts, fp, group}"""
    out: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for p in sorted(arch_dir.glob("*.json")):
        name = p.stem
        m = re.match(r"^(\d+)__(.+)$", name)
        if not m:
            continue
        uid, grp = m.group(1), m.group(2)
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        items = raw.get("items") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            continue
        turn_idx = 0
        for it in items:
            if not isinstance(it, dict):
                continue
            role = str(it.get("role") or "")
            text = str(it.get("text") or "")
            ts = it.get("telegram_ts")
            if ts is None:
                ts = None
            else:
                try:
                    ts = float(ts)
                except (TypeError, ValueError):
                    ts = None
            fp = fingerprint(text) if role == "user" and text.strip() else ""
            out[uid].append(
                {
                    "role": role,
                    "text": text,
                    "ts": ts,
                    "fp": fp,
                    "group": grp,
                    "turn_idx": turn_idx,
                    "archive_file": p.name,
                }
            )
            turn_idx += 1
    return out


def _index_by_fp(rows: List[Dict[str, Any]], *, uid_key: str = "user_id") -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
    idx: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        fp = str(r.get("fp") or "")
        if not fp:
            continue
        uid = str(r.get(uid_key) or "")
        if uid:
            idx[(uid, fp)].append(r)
        else:
            idx[("", fp)].append(r)
    return idx


def _load_logs(root: Path) -> Dict[str, Any]:
    data = root / "data"
    runtime = data / "runtime"
    llm_path = data / "llm_usage.jsonl"
    if not llm_path.is_file():
        llm_path = runtime / "llm_usage.jsonl"

    def collect(path: Path) -> List[Dict[str, Any]]:
        return list(_read_jsonl(path))

    cdc = collect(runtime / "cdc_turn_outcomes.jsonl")
    exp = collect(runtime / "experience_digest.jsonl")
    strat = collect(runtime / "strategy_paths.jsonl")
    rr = collect(runtime / "route_risk.jsonl")
    fb = collect(runtime / "user_feedback.jsonl")
    epi = collect(runtime / "episodic_memory.jsonl")
    llm = collect(llm_path)

    # llm by user from session_id u-{id}.eN.tag
    llm_by_user: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in llm:
        sid = str(r.get("session_id") or "")
        m = re.match(r"u-(\d+)\.", sid)
        if m:
            llm_by_user[m.group(1)].append(r)

    return {
        "cdc": cdc,
        "experience": exp,
        "strategy": strat,
        "route_risk": rr,
        "feedback": fb,
        "episodic": epi,
        "llm": llm,
        "llm_by_user": dict(llm_by_user),
    }


def _match_nearest(
    rows: List[Dict[str, Any]],
    *,
    ts: Optional[float],
    window_sec: float = 120.0,
) -> Optional[Dict[str, Any]]:
    if not rows:
        return None
    if ts is None:
        return rows[-1]
    best = None
    best_d = 1e18
    for r in rows:
        rt = _parse_ts(str(r.get("ts") or ""))
        if rt is None:
            continue
        d = abs(rt - ts)
        if d < best_d:
            best_d = d
            best = r
    if best is not None and best_d <= window_sec:
        return best
    return rows[-1] if len(rows) == 1 else best


def _llm_for_turn(
    llm_rows: List[Dict[str, Any]],
    ts: Optional[float],
    window_sec: float = 90.0,
) -> List[Dict[str, Any]]:
    if not llm_rows:
        return []
    if ts is None:
        return llm_rows[-3:]
    matched = []
    for r in llm_rows:
        rt = _parse_ts(str(r.get("ts") or ""))
        if rt is None:
            continue
        if abs(rt - ts) <= window_sec:
            matched.append(r)
    return matched or llm_rows[-2:]


def build_report(root: Path) -> str:
    arch_dir = _archive_dir(root)
    archives = _load_archives(arch_dir)
    logs = _load_logs(root)

    cdc_idx = _index_by_fp(logs["cdc"])
    exp_idx = _index_by_fp(logs["experience"])
    strat_idx = _index_by_fp(logs["strategy"])
    fb_fps = {
        (str(x.get("user_id") or ""), fingerprint(str(x.get("user_excerpt") or ""))): x
        for x in logs["feedback"]
    }

    lines: List[str] = []
    lines.append("# Реконструкция ходов: архивы × логи\n")
    lines.append(f"- Сгенерировано: `{datetime.now(timezone.utc).isoformat()}`")
    lines.append(f"- Корень данных: `{root}`")
    lines.append(f"- Архивы: `{arch_dir}` ({len(list(arch_dir.glob('*.json')))} файлов)\n")

    # Global stats
    total_user_msgs = 0
    matched_cdc = 0
    matched_exp = 0
    with_feedback = 0
    intent_counter: Counter = Counter()
    outcome_counter: Counter = Counter()
    tag_counter: Counter = Counter()
    negative_turns: List[Dict[str, Any]] = []

    for uid, turns in sorted(archives.items(), key=lambda x: -len(x[1])):
        user_msgs = [t for t in turns if t["role"] == "user" and t.get("fp")]
        if not user_msgs:
            continue
        total_user_msgs += len(user_msgs)

    for uid, turns in archives.items():
        user_msgs = [t for t in turns if t["role"] == "user" and t.get("fp")]
        for um in user_msgs:
            key = (uid, um["fp"])
            cdc_hits = cdc_idx.get(key, [])
            exp_hits = exp_idx.get(key, []) or exp_idx.get(("", um["fp"]), [])
            if cdc_hits:
                matched_cdc += 1
                for h in cdc_hits:
                    intent_counter[str(h.get("intent") or "?")] += 1
                    outcome_counter[str(h.get("outcome") or "?")] += 1
            if exp_hits:
                matched_exp += 1
            fb = fb_fps.get(key)
            if fb:
                with_feedback += 1
                if int(fb.get("score") or 0) < 0:
                    negative_turns.append(
                        {
                            "uid": uid,
                            "fp": um["fp"],
                            "text": um["text"][:200],
                            "fb": fb,
                            "cdc": cdc_hits[-1] if cdc_hits else None,
                            "exp": exp_hits[-1] if exp_hits else None,
                        }
                    )

            llm_hits = _llm_for_turn(logs["llm_by_user"].get(uid, []), um.get("ts"))
            for lh in llm_hits:
                tag_counter[str(lh.get("tag") or "?")] += 1

    lines.append("## 1. Сводка сопоставления\n")
    lines.append("| Метрика | Значение |")
    lines.append("|---------|----------|")
    lines.append(f"| Пользователей в архиве | {len(archives)} |")
    lines.append(f"| User-сообщений (с fp) | {total_user_msgs} |")
    lines.append(f"| Совпало с CDC | {matched_cdc} ({100*matched_cdc/max(1,total_user_msgs):.0f}%) |")
    lines.append(f"| Совпало с experience_digest | {matched_exp} ({100*matched_exp/max(1,total_user_msgs):.0f}%) |")
    lines.append(f"| Есть 👎/👍 в feedback | {with_feedback} |")
    lines.append(f"| Записей llm_usage | {len(logs['llm'])} |")
    lines.append(f"| route_risk | {len(logs['route_risk'])} |")
    lines.append("")

    lines.append("### Распределение intent (CDC, по совпавшим ходам)\n")
    for k, v in intent_counter.most_common(12):
        lines.append(f"- `{k}`: {v}")
    lines.append("\n### Распределение outcome (CDC)\n")
    for k, v in outcome_counter.most_common(8):
        lines.append(f"- `{k}`: {v}")
    lines.append("\n### Теги LLM (окно ±90с от user msg)\n")
    for k, v in tag_counter.most_common(15):
        lines.append(f"- `{k}`: {v}")
    lines.append("")

    # Per user
    lines.append("## 2. По пользователям\n")
    for uid, turns in sorted(archives.items(), key=lambda x: -len([t for t in x[1] if t["role"] == "user"])):
        user_msgs = [t for t in turns if t["role"] == "user"]
        asst = [t for t in turns if t["role"] == "assistant"]
        ts_min = min((t["ts"] for t in turns if t.get("ts")), default=None)
        ts_max = max((t["ts"] for t in turns if t.get("ts")), default=None)
        def _fmt_ts(t: Optional[float]) -> str:
            if t is None:
                return "?"
            return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
        lines.append(f"### user_id `{uid}` ({len(user_msgs)} user / {len(asst)} assistant)\n")
        lines.append(f"- Архив: `{turns[0]['archive_file'] if turns else '?'}`")
        lines.append(f"- Период (telegram_ts): {_fmt_ts(ts_min)} … {_fmt_ts(ts_max)}")
        lines.append(f"- Строк в архиве: {len(turns)}\n")

        # Sample last 5 user turns with reconstruction
        samples = [t for t in turns if t["role"] == "user" and t.get("fp")][-8:]
        if not samples:
            continue
        lines.append("<details><summary>Последние ходы (реконструкция)</summary>\n")
        for um in samples:
            key = (uid, um["fp"])
            cdc = cdc_idx.get(key, [])
            exp = exp_idx.get(key, [])
            strat = strat_idx.get(key, [])
            fb = fb_fps.get(key)
            # assistant reply following this user msg
            reply = ""
            idx = turns.index(um)
            for j in range(idx + 1, min(idx + 4, len(turns))):
                if turns[j]["role"] == "assistant":
                    reply = turns[j]["text"][:280]
                    break
            cdc_r = cdc[-1] if cdc else {}
            exp_r = exp[-1] if exp else {}
            strat_r = strat[-1] if strat else {}
            llm_h = _llm_for_turn(logs["llm_by_user"].get(uid, []), um.get("ts"))
            lines.append(f"\n**User** ({_fmt_ts(um.get('ts'))}): {um['text'][:160].replace(chr(10), ' ')}…\n")
            if reply:
                lines.append(f"- **Ответ (архив):** {reply.replace(chr(10), ' ')}…\n")
            if cdc_r:
                lines.append(
                    f"- **CDC:** intent=`{cdc_r.get('intent')}` module=`{cdc_r.get('module')}` "
                    f"outcome=`{cdc_r.get('outcome')}` skill=`{cdc_r.get('skill')}`\n"
                )
            if exp_r:
                lines.append(
                    f"- **Планировщик:** `{exp_r.get('planner_reason', '')[:80]}`\n"
                )
            if strat_r:
                lines.append(
                    f"- **Стратегия:** `{str(strat_r.get('steps_summary') or '')[:120]}`\n"
                )
            if llm_h:
                tags = ", ".join(
                    f"{x.get('tag')}({x.get('prompt_tokens')}p/{x.get('latency_ms')}ms)"
                    for x in llm_h[-3:]
                )
                lines.append(f"- **LLM:** {tags}\n")
            if fb:
                lines.append(f"- **Feedback:** score={fb.get('score')} source={fb.get('source')}\n")
        lines.append("\n</details>\n")

    # Negative feedback deep dive
    lines.append("## 3. Все 👎 (user_feedback) с реконструкцией\n")
    for item in sorted(negative_turns, key=lambda x: str((x["fb"] or {}).get("ts") or "")):
        fb = item["fb"]
        cdc = item.get("cdc") or {}
        exp = item.get("exp") or {}
        lines.append(f"\n### {fb.get('ts', '')[:19]} user `{item['uid']}`\n")
        lines.append(f"- **Текст:** {item['text']}\n")
        lines.append(
            f"- **Маршрут:** intent=`{cdc.get('intent', fb.get('intent'))}` "
            f"module=`{cdc.get('module', fb.get('module'))}` skill=`{cdc.get('skill', fb.get('skill'))}` "
            f"outcome=`{cdc.get('outcome', '?')}`\n"
        )
        if exp.get("planner_reason"):
            lines.append(f"- **planner_reason:** `{exp['planner_reason']}`\n")
        if exp.get("assistant_excerpt"):
            lines.append(f"- **Ответ (digest):** {str(exp['assistant_excerpt'])[:200]}…\n")

    # route_risk noise
    rr_out = Counter(str(x.get("outcome") or "?") for x in logs["route_risk"])
    lines.append("\n## 4. route_risk (весь журнал)\n")
    for k, v in rr_out.most_common():
        lines.append(f"- `{k}`: {v}")

    # Gaps
    lines.append("\n## 5. Разрывы в данных (честно)\n")
    lines.append(
        "- **Архив FIFO max ~500 строк** на сессию — ранние реплики (до мая) могли выпасть; "
        "полной истории «с самого начала» в message_archive нет.\n"
    )
    lines.append(
        "- **llm_usage** не хранит `user_text` — связь только через `session_id` + время ±90с или через fp в CDC/experience.\n"
    )
    lines.append(
        "- **route_risk** без user_id — только fp текста; совпадение с архивом по fp, не по пользователю.\n"
    )
    lines.append(
        "- **~32% user-сообщений** могут не иметь CDC, если ход был fast-path без записи или архив старше логов.\n"
    )
    unmatched = total_user_msgs - matched_cdc
    if total_user_msgs:
        lines.append(f"- Несопоставлено с CDC в этом прогоне: **~{unmatched}** из {total_user_msgs} user-msg.\n")

    return "\n".join(lines)


def main() -> int:
    default_root = str(Path(__file__).resolve().parents[1])
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=default_root)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    root = Path(args.root)
    report = build_report(root)
    if args.out:
        out_p = Path(args.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(report, encoding="utf-8")
        print(f"Wrote {out_p}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
