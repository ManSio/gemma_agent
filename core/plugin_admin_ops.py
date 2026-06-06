from __future__ import annotations

from pathlib import Path


def normalize_plugin_name(raw: str) -> str:
    return (raw or "").strip().strip("/").replace("\\", "/").split("/")[-1].strip()


def is_generated_plugin_name(name: str) -> bool:
    n = normalize_plugin_name(name)
    if not n.startswith("user_requested_plugin"):
        return False
    return all(ch.isalnum() or ch == "_" for ch in n)


def safe_plugin_dir(modules_root: Path, plugin_name: str) -> Path | None:
    name = normalize_plugin_name(plugin_name)
    if not name:
        return None
    base = modules_root.resolve()
    target = (base / name).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return None
    return target
