#!/usr/bin/env python3
"""Fix OPENROUTER_GEN_STOP in .env — backticks break bash source."""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from fix_env_bash_source import fix_file  # noqa: E402


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else ".env")
    return fix_file(path)


if __name__ == "__main__":
    raise SystemExit(main())
