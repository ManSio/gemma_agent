from __future__ import annotations

import logging
import os
from typing import Any, Dict

from core.error_analysis import record_error_event

logger = logging.getLogger(__name__)


class SecurityManager:
    def evaluate(self, *, flood: Dict[str, Any], link_safety: Dict[str, Any], file_context: Dict[str, Any]) -> Dict[str, Any]:
        issues = []
        if flood.get("blocked"):
            issues.append({"type": "flood", "severity": "medium", "details": flood})
        if link_safety.get("worst") in {"suspicious", "dangerous"}:
            issues.append({"type": "link_safety", "severity": "high" if link_safety.get("worst") == "dangerous" else "medium", "details": link_safety})
        if isinstance(file_context, dict) and file_context.get("error"):
            issues.append({"type": "file_intake", "severity": "medium", "details": file_context})
        level = "ok"
        if any(i["severity"] == "high" for i in issues):
            level = "high_risk"
        elif issues:
            level = "warning"
        result = {"level": level, "issues": issues}
        if issues:
            # warning (ссылки/файл) — по умолчанию не в runtime_errors (шум → safe mode).
            journal_warn = os.getenv("SECURITY_JOURNAL_WARNINGS", "").strip().lower() in {"1", "true", "yes", "on"}
            if level == "high_risk" or journal_warn:
                record_error_event(
                    "security_manager",
                    "security evaluation issues",
                    extra={"code": "SECURITY_EVAL_ISSUES", "issues": issues, "level": level},
                    severity="warning",
                )
            else:
                logger.info("security evaluation issues (not journaled): level=%s n=%s", level, len(issues))
        return result
