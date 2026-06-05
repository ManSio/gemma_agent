"""Fire-and-forget asyncio tasks with logged failures (no silent task exceptions)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine, Optional, Set

logger = logging.getLogger(__name__)

_background_tasks: Set[asyncio.Task[Any]] = set()


def _on_task_done(task: asyncio.Task[Any], label: str) -> None:
    _background_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("background task %s failed: %s", label, exc, exc_info=True)


def spawn_logged(
    coro: Coroutine[Any, Any, Any],
    *,
    label: str,
    loop: Optional[asyncio.AbstractEventLoop] = None,
) -> asyncio.Task[Any]:
    """
    Schedule coroutine; log any uncaught exception (stdlib create_task drops them).
    Keeps a weak set so tasks are not GC'd before completion.
    """
    if loop is None:
        loop = asyncio.get_running_loop()
    task = loop.create_task(coro, name=label[:40] if label else None)
    _background_tasks.add(task)
    task.add_done_callback(lambda t, lbl=label: _on_task_done(t, lbl))
    return task
