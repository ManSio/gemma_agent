"""
Семвер по conventional commits (упрощённо) + общие хелперы для релизных скриптов.

Правила (для одного коммита):
- BREAKING CHANGE в теле, «!» после типа, или тип с ! → major
- feat → minor
- fix, perf, revert → patch
- docs, style, test, chore, build, ci, refactor → не влияют на версию (None)
- непохожее на conventional → None (дальше решает fallback в скрипте)
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional

BumpKind = Literal["major", "minor", "patch"]

# MAJOR[.MINOR[.PATCH]] + необязательный хвост (-beta, +build) — как в манифестах «1.0» без третьей цифры
_LOOSE_NUMERIC_PREFIX = re.compile(r"^(\d+)(?:\.(\d+)(?:\.(\d+))?)?(.*)$")

_TYPE_LINE = re.compile(
    r"^(?P<type>[a-z]+)(?P<scope>\([^)]*\))?(?P<bang>!)\s*:\s*(?P<subj>.+)$",
    re.IGNORECASE,
)
# Без bang после scope — второй паттерн
_TYPE_LINE_NB = re.compile(
    r"^(?P<type>[a-z]+)(?P<scope>\([^)]*\))?\s*:\s*(?P<subj>.+)$",
    re.IGNORECASE,
)


def _parse_loose_semver(version: str) -> tuple[int, int, int, str]:
    v = version.strip()
    m = _LOOSE_NUMERIC_PREFIX.match(v)
    if not m:
        raise ValueError(f"Ожидался semver (например 1.0.0 или 1.0), получено: {version!r}")
    major = int(m.group(1))
    minor = int(m.group(2) or "0")
    patch = int(m.group(3) or "0")
    suffix = m.group(4) or ""
    return major, minor, patch, suffix


def bump_semver(version: str, kind: BumpKind) -> str:
    major, minor, patch, suffix = _parse_loose_semver(version)
    if kind == "patch":
        return f"{major}.{minor}.{patch + 1}{suffix}"
    if kind == "minor":
        return f"{major}.{minor + 1}.0{suffix}"
    if kind == "major":
        return f"{major + 1}.0.0{suffix}"
    raise ValueError(kind)


def strip_bundled(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in d.items() if k != "bundled_with"}


def parse_conventional_bump(subject: str, body: str) -> Optional[BumpKind]:
    subj = (subject or "").strip()
    body_u = (body or "").strip()
    if "BREAKING CHANGE" in body_u or "BREAKING-CHANGE" in body_u.upper():
        return "major"
    low = subj.lower()
    if low.startswith("breaking") and ":" in subj:
        return "major"

    m = _TYPE_LINE.match(subj)
    if m:
        return "major"
    m = _TYPE_LINE_NB.match(subj)
    if not m:
        return None
    typ = m.group("type").lower()
    if typ == "feat":
        return "minor"
    if typ in ("fix", "perf", "revert"):
        return "patch"
    if typ in ("docs", "style", "test", "chore", "build", "ci", "refactor"):
        return None
    return None


def max_bump(kinds: List[Optional[BumpKind]]) -> Optional[BumpKind]:
    order = {"patch": 0, "minor": 1, "major": 2}
    best: Optional[BumpKind] = None
    for k in kinds:
        if k is None:
            continue
        if best is None or order[k] > order[best]:
            best = k
    return best


# Ядро приложения: изменения здесь → корневой VERSION
APP_PATH_PREFIXES = (
    "core/",
    "main.py",
    "api.py",
    "requirements.txt",
    "pyproject.toml",
    "Dockerfile",
)


def is_app_path(path: str) -> bool:
    p = path.replace("\\", "/").strip()
    if not p:
        return False
    if p.startswith("core/__pycache__/"):
        return False
    if p.startswith("core/"):
        return True
    if p in ("main.py", "api.py", "requirements.txt", "pyproject.toml", "Dockerfile"):
        return True
    return False


def module_root_for_path(path: str) -> Optional[str]:
    path = path.replace("\\", "/").strip()
    if not path:
        return None
    parts = path.split("/")
    if len(parts) >= 2 and parts[0] in ("modules", "core_libraries"):
        return f"{parts[0]}/{parts[1]}"
    return None
