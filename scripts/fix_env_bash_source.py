#!/usr/bin/env python3
"""
Починить .env для bash `source`: значения с пробелами/спецсymbolами — в одинарных кавычках.

  ./venv/bin/python scripts/fix_env_bash_source.py .env
  bash -c 'set -a; source .env; set +a'   # без «command not found»
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Явные правки (исторические инциденты)
_EXACT: dict[str, str] = {
    "OPENROUTER_GEN_STOP": "'```||</answer>'",
    "OPENROUTER_X_TITLE": "'Gemma Agent'",
    "USER_ACCESS_PENDING_MESSAGE": "'⏳ Заявка отправлена администратору. Ожидайте подтверждения.'",
    "USER_ACCESS_BLOCKED_MESSAGE": "'⛔ Доступ к боту для этого аккаунта закрыт.'",
    "USER_ACCESS_GUEST_QUOTA_EXHAUSTED_MESSAGE": (
        "'⏳ Лимит пробных ответов исчерпан. Дождитесь подтверждения администратора.'"
    ),
    "ANTI_FLOOD_RESPONSE": "'Слишком много сообщений подряд. Подожди немного.'",
    "GEMMA_INSTANCE_CREDIT_LINE": "'Этот бот настроил …'",
}

_ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_ORPHAN_ADMIN = re.compile(r"^,\d+\s*$")
_CYRILLIC = re.compile(r"[\u0400-\u04FF]")
_QUOTE_SUFFIXES = (
    "_MESSAGE",
    "_RESPONSE",
    "_LABEL",
    "_LINE",
    "_COMMAND",
    "_PLACEHOLDER",
    "_TEXT",
    "_TITLE",
)


def _is_quoted(val: str) -> bool:
    return len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"')


def _split_value_comment(val: str) -> tuple[str, str]:
    """Отделить значение от inline-комментария « # ...» (только вне кавычек)."""
    in_single = False
    in_double = False
    i = 0
    while i < len(val):
        ch = val[i]
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double and i > 0 and val[i - 1] in (" ", "\t"):
            return val[: i - 1].rstrip(), val[i - 1 :]
        i += 1
    return val.rstrip(), ""


def _unquote(val: str) -> str:
    if _is_quoted(val):
        return val[1:-1]
    return val


def _quote_single(val: str) -> str:
    if _is_quoted(val):
        return val
    escaped = val.replace("'", "'\"'\"'")
    return f"'{escaped}'"


def _needs_quote(key: str, val: str) -> bool:
    if key in _EXACT:
        return True
    if not val or _is_quoted(val):
        return False
    if " " in val or "\t" in val:
        return True
    if _CYRILLIC.search(val):
        return True
    if any(key.endswith(s) for s in _QUOTE_SUFFIXES):
        return True
    if any(ch in val for ch in ("`", "$", "!", "(", ")", "&", "|", ";", "<", ">")):
        return True
    return False


def fix_line(line: str) -> tuple[str, bool | None]:
    stripped = line.strip()
    if _ORPHAN_ADMIN.match(stripped):
        return "", True
    if not stripped or stripped.startswith("#"):
        return line, False
    m = _ASSIGN_RE.match(stripped)
    if not m:
        return line, False
    key, raw_val = m.group(1), m.group(2)

    if key in _EXACT:
        new_val = _EXACT[key]
        val_part, comment_suffix = _split_value_comment(raw_val)
        _ = val_part  # ignore old
        target = f"{key}={new_val}{comment_suffix}"
        if stripped == target:
            return line, False
        new_line = target
        if line.endswith("\n"):
            new_line += "\n"
        elif line.endswith("\r"):
            new_line += "\r"
        return new_line, True

    val_part, comment_suffix = _split_value_comment(raw_val)
    bare = _unquote(val_part)

    if not _needs_quote(key, bare):
        # Убрать лишние кавычки (например после ошибочного прогона)
        if _is_quoted(val_part) and bare == _unquote(val_part):
            restored = f"{key}={bare}{comment_suffix}"
            if stripped != restored:
                new_line = restored
                if line.endswith("\n"):
                    new_line += "\n"
                return new_line, True
        return line, False

    quoted = _quote_single(bare)
    new_stripped = f"{key}={quoted}{comment_suffix}"
    if stripped == new_stripped:
        return line, False
    new_line = new_stripped
    if line.endswith("\n"):
        new_line += "\n"
    elif line.endswith("\r"):
        new_line += "\r"
    return new_line, True


def fix_file(path: Path) -> int:
    if not path.is_file():
        print(f"missing {path}", file=sys.stderr)
        return 1
    raw = path.read_text(encoding="utf-8", errors="replace")
    lines = raw.splitlines(keepends=True)

    changed = 0
    out: list[str] = []
    for line in lines:
        bare = line.rstrip("\n\r")
        suffix = line[len(bare) :]
        fixed, did = fix_line(bare)
        if did is True and not fixed:
            changed += 1
            continue
        if did:
            changed += 1
            m = _ASSIGN_RE.match(fixed.strip())
            print(f"  fix: {m.group(1) if m else bare[:40]}...")
        out.append(fixed + suffix)

    text = "".join(out)
    if text and not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8")
    print(f"OK: {path} ({changed} line(s) fixed)")
    return 0


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else ".env")
    return fix_file(path)


if __name__ == "__main__":
    raise SystemExit(main())
