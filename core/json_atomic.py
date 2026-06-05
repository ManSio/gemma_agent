"""Атомарная запись JSON-файлов (общий для plugin/core storage)."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Optional, Union

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]


def read_json_file(path: PathLike, default: Any) -> Any:
    p = Path(path)
    if not p.is_file():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("[json_atomic] read failed path=%s", p)
        return default


def atomic_write_json(path: PathLike, data: Any, *, indent: Optional[int] = 2) -> bool:
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        kwargs: dict = {"ensure_ascii": False}
        if indent is not None:
            kwargs["indent"] = indent
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=str(p.parent) or ".",
            suffix=".tmp",
            delete=False,
            encoding="utf-8",
        ) as tf:
            json.dump(data, tf, **kwargs)
            tmppath = tf.name
        os.replace(tmppath, p)
        return True
    except Exception:
        logger.exception("[json_atomic] write failed path=%s", p)
        return False
