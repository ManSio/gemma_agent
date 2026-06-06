"""Public build: каталог модулей (tier A+B only)."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "config" / "modules_catalog.json"


def test_modules_catalog_matches_disk() -> None:
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    modules = data.get("modules") or {}
    disk = list((ROOT / "modules").glob("*/module.json"))
    assert len(modules) == len(disk)
    assert len(modules) >= 10
    for name, meta in modules.items():
        folder = (meta.get("folder") or name).replace("-", "_")
        assert (ROOT / "modules" / folder / "module.json").is_file(), name


def test_every_manifest_has_gemma_tier() -> None:
    for mj in (ROOT / "modules").glob("*/module.json"):
        m = json.loads(mj.read_text(encoding="utf-8"))
        tier = (m.get("gemma_tier") or "").strip()
        assert tier in {"A", "B", "C", "D", "DEV"}, f"{mj}: missing gemma_tier"


def test_default_denylist_subset_of_modules() -> None:
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    deny = set(data.get("default_denylist") or [])
    names = set((data.get("modules") or {}).keys())
    assert deny <= names
