"""Send-path: user-reply в Telegram идёт через finalize в _send_output (Claude audit)."""

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def test_send_output_calls_finalize_user_reply() -> None:
    src = (_ROOT / "core" / "input_layer.py").read_text(encoding="utf-8")
    start = src.find("async def _send_output(")
    assert start >= 0
    chunk = src[start : start + 14000]
    fin = chunk.find("txt = finalize_user_reply")
    assert fin >= 0
    send = chunk.find("await reply_text_chunks", fin)
    assert send >= 0, "main text path must send after finalize"


def test_pre_llm_variants_subset_documented_in_tests() -> None:
    """Инвариант pre_llm ⊆ orchestrator — tests/test_pre_llm_plan.py."""
    t = (_ROOT / "tests" / "test_pre_llm_plan.py").read_text(encoding="utf-8")
    assert "PRE_LLM_DIRECT_VARIANTS" in t
    assert "_FALLBACK_DIRECT_REPLY_VARIANTS" in t
