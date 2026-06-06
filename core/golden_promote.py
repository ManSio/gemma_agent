"""E3: правила promote в golden_corpus — только PASS + чистая chain."""
from __future__ import annotations

from typing import Any, Dict, List


def chain_passes_for_golden(row: Dict[str, Any]) -> bool:
    """Строка agent_test report годится для golden_corpus."""
    if not row.get("pass"):
        return False
    chain = row.get("chain") if isinstance(row.get("chain"), dict) else {}
    if not chain:
        return False
    leaks = chain.get("leaks") if isinstance(chain.get("leaks"), dict) else {}
    if leaks.get("reply"):
        return False
    quality = chain.get("quality") if isinstance(chain.get("quality"), dict) else {}
    if quality.get("issues"):
        return False
    errors = chain.get("errors")
    if isinstance(errors, list) and errors:
        return False
    return True


def golden_record_from_report_row(row: Dict[str, Any], *, ts: str = "") -> Dict[str, Any]:
    chain = row.get("chain") or {}
    after = chain.get("after_execute") or {}
    return {
        "id": str(row.get("id") or ""),
        "ts": ts or row.get("ts") or "",
        "source": row.get("source") or "agent_test_pass",
        "user_text": row.get("user_text"),
        "reply_preview": row.get("reply_preview"),
        "profile": after.get("brain_profile") or after.get("router_profile"),
        "module": after.get("planned_module"),
        "intent": after.get("last_intent"),
        "llm_calls": chain.get("llm_calls"),
        "tags": row.get("tags") or [],
        "status": "golden_verified",
    }
