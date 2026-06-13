"""Request correlation ID for API and orchestrator log tracing."""
from __future__ import annotations

import contextvars
import uuid
from typing import Optional

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("gemma_request_id", default="")


def new_request_id() -> str:
    """Generate a short correlation id for one HTTP/Telegram turn."""
    return uuid.uuid4().hex[:16]


def get_request_id() -> str:
    """Current correlation id or empty string."""
    return _request_id.get()


def set_request_id(request_id: str) -> contextvars.Token[str]:
    """Bind correlation id to the current async/task context."""
    return _request_id.set(str(request_id or "").strip())


def reset_request_id(token: contextvars.Token[str]) -> None:
    """Restore previous correlation id after middleware/handler."""
    _request_id.reset(token)


def ensure_request_id(existing: Optional[str] = None) -> str:
    """Return existing context id or create and bind a new one."""
    rid = (existing or "").strip() or get_request_id()
    if rid:
        return rid
    rid = new_request_id()
    set_request_id(rid)
    return rid
