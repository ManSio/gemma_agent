from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional


@dataclass
class UnifiedError:
    code: str
    component: str
    message: str
    severity: str = "error"
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    error_type: str = ""
    error: str = ""
    context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def make_error(
    *,
    code: str,
    component: str,
    message: str,
    severity: str = "error",
    exc: Optional[BaseException] = None,
    context: Optional[Dict[str, Any]] = None,
) -> UnifiedError:
    err = UnifiedError(code=code, component=component, message=message, severity=severity, context=context or {})
    if exc is not None:
        err.error_type = type(exc).__name__
        err.error = str(exc)
    return err
