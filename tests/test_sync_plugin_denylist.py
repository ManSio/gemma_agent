"""sync_plugin_denylist_env: merge catalog + existing .env."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from scripts.sync_plugin_denylist_env import KEY, _apply_to_env, merged_denylist

ROOT = Path(__file__).resolve().parent.parent


def test_merge_keeps_extra_and_catalog() -> None:
    catalog = json.loads((ROOT / "config" / "modules_catalog.json").read_text(encoding="utf-8"))
    default = set(catalog.get("default_denylist") or [])
    env = "PLUGIN_CONTROLLER_DENYLIST=books_rag,heavy_module\n"
    got = set(merged_denylist(env))
    assert default <= got
    assert "books_rag" in got
    assert "heavy_module" in got
    assert len(got) >= len(default) + 1


def test_apply_to_env_creates_missing_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        env = Path(tmp) / ".env"
        assert not env.is_file()
        names = ["echo", "benchmark_runner"]
        _apply_to_env(env, names, dry_run=False, existing_text="")
        assert env.is_file()
        body = env.read_text(encoding="utf-8")
        assert body.startswith(f"{KEY}=echo,benchmark_runner")
