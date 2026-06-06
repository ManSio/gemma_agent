"""
Ops API: наблюдение за ходами в реальном времени (SSE) и снимки контекста.

Подключение: api.py → include_router(ops_router)
Авторизация: X-API-Token или Authorization: Bearer (тот же API_TOKEN).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from core.ops_trace import (
    load_user_dialogue_view,
    log_path,
    ops_trace_enabled,
    read_tail,
    record_ops_turn,
)

logger = logging.getLogger(__name__)

ops_router = APIRouter(prefix="/api/v1/ops", tags=["ops"])


from core.api_auth import verify_api_token


class OpsProbeRequest(BaseModel):
    user_id: str
    message: str
    group_id: Optional[str] = None
    channel: str = "ops_probe"


class OpsProbeResponse(BaseModel):
    ok: bool
    user_id: str
    user_text: str
    assistant_text: str
    responses: List[str]
    plan_steps: int
    metadata: Dict[str, Any]
    trace: Dict[str, Any]


@ops_router.get("/ping")
async def ops_ping(_token: str = Depends(verify_api_token)):
    """Проверка токена без тяжёлого diagnostics."""
    return {
        "ok": True,
        "ops_trace_enabled": ops_trace_enabled(),
        "ops_trace_path": str(log_path()),
    }


@ops_router.get("/turns")
async def ops_turns(
    limit: int = Query(40, ge=1, le=500),
    user_id: Optional[str] = Query(None),
    since_ts: Optional[str] = Query(None, description="ISO timestamp, only newer rows"),
    issues_only: bool = Query(False),
    _token: str = Depends(verify_api_token),
):
    rows = read_tail(limit=limit, user_id=user_id, since_ts=since_ts)
    if issues_only:
        rows = [r for r in rows if r.get("issues")]
    return {"count": len(rows), "turns": rows}


@ops_router.get("/turns/stream")
async def ops_turns_stream(
    user_id: Optional[str] = Query(None),
    poll_ms: int = Query(400, ge=100, le=3000),
    _token: str = Depends(verify_api_token),
):
    """
    Server-Sent Events: новые строки ops_trace.jsonl в реальном времени.
    Клиент: curl -N -H 'X-API-Token: ...' 'http://host:8000/api/v1/ops/turns/stream'
    """

    path = log_path()

    async def _gen() -> AsyncIterator[str]:
        pos = 0
        if path.is_file():
            pos = path.stat().st_size
        yield f"data: {json.dumps({'event': 'connected', 'path': str(path)}, ensure_ascii=False)}\n\n"
        while True:
            try:
                if path.is_file():
                    size = path.stat().st_size
                    if size > pos:
                        with open(path, "r", encoding="utf-8", errors="replace") as f:
                            f.seek(pos)
                            chunk = f.read()
                            pos = f.tell()
                        for line in chunk.splitlines():
                            if not line.strip():
                                continue
                            try:
                                row = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            if user_id and str(row.get("user_id") or "") != str(user_id):
                                continue
                            yield f"data: {json.dumps(row, ensure_ascii=False, default=str)}\n\n"
            except asyncio.CancelledError:
                break
            except OSError:
                logger.debug("ops stream read failed", exc_info=True)
                yield f"data: {json.dumps({'event': 'error'}, ensure_ascii=False)}\n\n"
            await asyncio.sleep(poll_ms / 1000.0)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@ops_router.get("/users/{user_id}/dialogue")
async def ops_user_dialogue(
    user_id: str,
    group_id: Optional[str] = Query(None),
    _token: str = Depends(verify_api_token),
):
    return load_user_dialogue_view(user_id, group_id)


@ops_router.post("/probe", response_model=OpsProbeResponse)
async def ops_probe(
    http_request: Request,
    body: OpsProbeRequest,
    _token: str = Depends(verify_api_token),
):
    """
    Полный ход через оркестратор + запись в ops_trace (как Telegram, но с трассой в ответе).
    """
    from core.api_rate_limit import assert_api_heavy_rate_limit

    await assert_api_heavy_rate_limit(http_request, user_id=body.user_id, scope="ops_probe")
    from core.api_state import get_orchestrator
    from core.models import Input

    orchestrator = get_orchestrator()
    if orchestrator is None:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")
    from core.message_archive import items_for_prompt

    uid = str(body.user_id)
    gid = body.group_id
    text = str(body.message or "")
    rec_before = orchestrator.behavior_store.load(uid, gid) if orchestrator.behavior_store else {}
    rm_before = (rec_before.get("recent_messages") if isinstance(rec_before, dict) else []) or []
    arch_before = items_for_prompt(uid, gid)

    meta = {"channel": body.channel, "ops_probe": True}
    inp = Input(type="text", payload=text, meta=meta)
    plan = orchestrator.plan(inp, uid, gid)
    outputs = await orchestrator.execute_plan(plan, uid, gid)
    texts: List[str] = []
    for o in outputs or []:
        if getattr(o, "type", None) == "text" and str(getattr(o, "payload", "") or "").strip():
            texts.append(str(o.payload).strip())
    assistant = texts[0] if texts else ""

    rec_after = orchestrator.behavior_store.load(uid, gid) if orchestrator.behavior_store else {}
    rm_after = (rec_after.get("recent_messages") if isinstance(rec_after, dict) else []) or []

    rs: Dict[str, Any] = {}
    if plan.steps:
        ctx = (plan.steps[0].args or {}).get("context") or {}
        if isinstance(ctx, dict):
            raw = ctx.get("reasoning_state")
            if isinstance(raw, dict):
                rs = {
                    "intent": raw.get("intent"),
                    "mode": raw.get("mode"),
                    "reason": raw.get("reason"),
                }

    trace_row = record_ops_turn(
        user_id=uid,
        group_id=gid,
        channel=body.channel,
        user_text=text,
        assistant_text=assistant,
        recent_before=rm_before,
        recent_after=rm_after,
        archive_tail=arch_before,
        plan_steps=[s.module_name for s in plan.steps] if plan.steps else [],
        reasoning=rs,
        extra={"source": "ops_probe"},
    )

    return OpsProbeResponse(
        ok=bool(texts) and not trace_row.get("issues"),
        user_id=uid,
        user_text=text,
        assistant_text=assistant,
        responses=texts,
        plan_steps=len(plan.steps) if plan.steps else 0,
        metadata={"outputs_count": len(outputs or [])},
        trace=trace_row,
    )
