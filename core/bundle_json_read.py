"""
Сжатое и выборочное чтение bundle.json из диагностического ZIP (/zip_read).
По умолчанию — сводка вместо целого JSON, чтобы не раздувать второй проход мозга.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

_HEADER_RE = re.compile(r"^===\s+.+\s+===\s*\n", re.M)


def parse_zip_inner_spec(spec: str) -> Tuple[str, Dict[str, str]]:
    """
    Разбор хвоста /zip_read: «bundle.json section=env full=1», «section=performance».
    Возвращает (имя_файла_внутри_zip, опции).
    """
    spec = (spec or "").strip()
    if not spec:
        return "", {}
    parts: List[str] = []
    opts: Dict[str, str] = {}
    for token in spec.split():
        if "=" in token:
            k, _, v = token.partition("=")
            k = k.strip().lower()
            v = v.strip()
            if k:
                opts[k] = v
        else:
            parts.append(token)
    inner = parts[0] if parts else ""
    return inner, opts


def _truthy(v: str) -> bool:
    return (v or "").strip().lower() in {"1", "true", "yes", "on", "full"}


def bundle_json_default_full() -> bool:
    return (os.getenv("TOOLS_BUNDLE_JSON_DEFAULT_MODE") or "summary").strip().lower() == "full"


def _get_path(obj: Any, dotted: str) -> Any:
    cur: Any = obj
    for part in (dotted or "").split("."):
        part = part.strip()
        if not part:
            continue
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _json_snippet(val: Any, budget: int) -> str:
    try:
        s = json.dumps(val, ensure_ascii=False, default=str, indent=2)
    except Exception:
        s = str(val)
    if len(s) <= budget:
        return s
    half = max(100, budget // 2 - 40)
    return s[:half] + "\n… [обрезано] …\n" + s[-half:]


def _summary_for_bundle(b: Dict[str, Any], max_chars: int) -> str:
    try:
        msummary = int((os.getenv("TOOLS_BUNDLE_SUMMARY_SECTION_BUDGET") or "1200").strip())
    except ValueError:
        msummary = 1200
    lines: List[str] = [
        "Режим: сводка bundle.json (полный JSON: full=1 или mode=full).",
        f"bundle_version: {b.get('bundle_version')}",
        f"generated_utc: {b.get('generated_utc')}",
        f"Ключи верхнего уровня: {', '.join(sorted(b.keys()))}",
        "",
    ]
    priority_keys = [
        "boot_timeline",
        "env",
        "logging",
        "tools",
        "openrouter",
        "plugins",
        "performance",
        "diagnostic_snapshot",
        "admin_full_system_report",
        "runtime_errors_recent",
        "connectivity",
        "voice",
        "mem0_operator",
        "code_cartography",
    ]
    shown: set[str] = set()
    for key in priority_keys:
        if key not in b:
            continue
        shown.add(key)
        val = b[key]
        lines.append(f"--- {key} ---")
        if key == "runtime_errors_recent" and isinstance(val, list):
            tail = val[-8:] if len(val) > 8 else val
            lines.append(_json_snippet(tail, msummary))
        else:
            lines.append(_json_snippet(val, msummary))
        lines.append("")
    for key in sorted(k for k in b.keys() if k not in shown)[:16]:
        val = b[key]
        lines.append(f"--- {key} ---")
        lines.append(_json_snippet(val, min(msummary, 600)))
        lines.append("")
    out = "\n".join(lines).strip()
    if len(out) > max_chars:
        out = out[: max_chars - 60] + "\n… [сводка обрезана по TOOLS_BUNDLE_JSON_SUMMARY_MAX_CHARS] …"
    return out


def shape_bundle_json_payload(
    raw_text: str,
    options: Dict[str, str],
    *,
    member_label: str,
) -> str:
    """
    raw_text — содержимое файла (с опциональной строкой заголовка === ... ===).
    """
    text = raw_text
    m = _HEADER_RE.match(text)
    if m:
        text = text[m.end() :]
    text = text.strip()
    if not text:
        return raw_text

    try:
        bundle = json.loads(text)
    except json.JSONDecodeError:
        return raw_text

    if not isinstance(bundle, dict):
        return raw_text

    try:
        smax = int((os.getenv("TOOLS_BUNDLE_JSON_SUMMARY_MAX_CHARS") or "16000").strip())
    except ValueError:
        smax = 16000
    try:
        secmax = int((os.getenv("TOOLS_BUNDLE_JSON_SECTION_MAX_CHARS") or "28000").strip())
    except ValueError:
        secmax = 28000

    chunk = (options.get("chunk") or options.get("part") or "").strip()

    want_full_explicit = _truthy(options.get("full") or "") or options.get("mode", "").strip().lower() == "full"
    want_summary_explicit = options.get("mode", "").strip().lower() == "summary"
    if bundle_json_default_full():
        use_full = not want_summary_explicit
        if want_full_explicit:
            use_full = True
    else:
        use_full = want_full_explicit

    section = (options.get("section") or "").strip()
    path = (options.get("path") or "").strip()

    if path:
        got = _get_path(bundle, path)
        if got is None:
            body = f"path={path!r}: не найдено в bundle.json"
        else:
            body = _json_snippet(got, secmax)
        body = f"=== {member_label} (фрагмент path={path}) ===\n{body}"
    elif section:
        got = bundle.get(section)
        if got is None:
            body = f"section={section!r}: нет такого ключа"
        else:
            body = f"=== {member_label} :: {section} ===\n" + _json_snippet(got, secmax)
    elif use_full:
        body = f"=== {member_label} (полный JSON, mode=full) ===\n" + text
    else:
        body = f"=== {member_label} (сводка) ===\n" + _summary_for_bundle(bundle, smax)

    if chunk and "/" in chunk:
        a, _, b = chunk.partition("/")
        try:
            i, n = int(a.strip()), int(b.strip())
            if n > 0 and 0 < i <= n:
                size = max(1, (len(body) + n - 1) // n)
                start = (i - 1) * size
                end = min(i * size, len(body))
                body = (
                    f"[Фрагмент текста {i}/{n}, символы {start}–{end} из {len(body)}]\n\n" + body[start:end]
                )
        except ValueError:
            pass
    return body


def is_bundle_json_member(name: str) -> bool:
    n = (name or "").replace("\\", "/").split("/")[-1].strip().lower()
    return n == "bundle.json"
