"""
Инструмент мозга: выборка из архива переписки / digest / Mem0 (как /dialog_recall), без плагина modules/.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional


def _env_on(name: str, *, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class DialogRecallModule:
    """
    TOOL_CALL: DialogRecall.recall_bundle — когда нужны сохранённые реплики этой переписки,
    а в recent_dialogue мало контекста.
    """

    BRAIN_LITE_INCLUDE = True

    async def recall_bundle(
        self,
        user_id: str,
        mode: str = "summary",
        query: str = "",
        archive_tail: int = 28,
        group_id: Optional[str] = None,
        recall_context: Optional[Dict[str, Any]] = None,
        window_pick: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not _env_on("DIALOG_MEMORY_RECALL_ENABLED", default=True):
            return {"error": "dialog recall отключён (DIALOG_MEMORY_RECALL_ENABLED=false)."}
        if not _env_on("DIALOG_RECALL_BRAIN_TOOL_ENABLED", default=True):
            return {"error": "инструмент DialogRecall отключён (DIALOG_RECALL_BRAIN_TOOL_ENABLED=false)."}

        from core.memory_recall_facade import build_slash_recall_bundle

        ctx: Dict[str, Any] = dict(recall_context) if isinstance(recall_context, dict) else {}
        gid = (str(group_id).strip() if group_id is not None else "") or None
        try:
            tail = int(archive_tail)
        except (TypeError, ValueError):
            tail = 28
        tail = max(4, min(150, tail))

        mode_n = (mode or "summary").strip().lower()
        if mode_n in ("когда",):
            mode_n = "when"
        if mode_n not in ("summary", "archive", "search", "when"):
            mode_n = "summary"

        body = build_slash_recall_bundle(
            user_id=str(user_id),
            group_id=gid,
            context=ctx,
            mode=mode_n,
            query=str(query or "").strip(),
            archive_tail=tail,
            window_pick=window_pick,
        )
        return {"text": body, "mode": mode_n}
