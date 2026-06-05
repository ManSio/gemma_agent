"""reform_s9 must not run as fake §9 acceptance."""

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts"))

_spec = importlib.util.spec_from_file_location(
    "agent_telegram_client",
    _ROOT / "scripts" / "agent_telegram_client.py",
)
mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(mod)


def test_reform_s9_not_in_active_suites():
    assert "reform_s9" not in mod.SUITE_CHOICES
    assert "reform_s9" in mod.DEPRECATED_SUITES


def test_deprecated_message_mentions_tracker():
    msg = mod._deprecated_suite_message("reform_s9")
    assert "REFORM_S9_ACCEPTANCE_TRACKER" in msg
    assert "deploy-smoke" in msg
