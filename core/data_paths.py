"""Единый корень данных поведения (behavior + message_archive)."""
from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    pr = (os.getenv("PROJECT_ROOT") or os.getenv("GEMMA_PROJECT_ROOT") or "").strip()
    if pr:
        return Path(pr).resolve()
    return Path.cwd().resolve()


def behavior_data_root() -> Path:
    """Совпадает с BehaviorStore.base_dir и message_archive._base_dir."""
    raw = (os.getenv("BEHAVIOR_DATA_DIR") or "").strip()
    if raw:
        p = Path(raw)
        if not p.is_absolute():
            p = project_root() / p
        return p.resolve()
    return (project_root() / "data").resolve()


def behavior_dir() -> Path:
    return behavior_data_root() / "behavior"


def message_archive_dir() -> Path:
    return behavior_dir() / "message_archive"
