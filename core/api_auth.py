"""Общая авторизация HTTP API (api.py, core/api_ops.py)."""
from __future__ import annotations

import os
from typing import Optional, Set

from fastapi import Header, HTTPException, Query


def normalize_api_token(raw: Optional[str]) -> str:
    return (raw or "").strip().strip('"').strip("'")


def allowed_api_tokens() -> Set[str]:
    primary = normalize_api_token(os.getenv("API_TOKEN", "your_secure_api_token_here"))
    relay = normalize_api_token(os.getenv("BOT_RELAY_API_TOKEN", ""))
    out = {primary}
    if relay:
        out.add(relay)
    return out


def verify_api_token(
    token: Optional[str] = Query(None, description="API token (query)"),
    x_api_token: Optional[str] = Header(None, alias="X-API-Token"),
    authorization: Optional[str] = Header(None),
) -> str:
    raw: Optional[str] = None
    if x_api_token and str(x_api_token).strip():
        raw = normalize_api_token(x_api_token)
    elif authorization:
        a = authorization.strip()
        if a.lower().startswith("bearer "):
            raw = normalize_api_token(a[7:])
    if not raw and token:
        raw = normalize_api_token(token)
    if not raw or raw not in allowed_api_tokens():
        raise HTTPException(status_code=401, detail="Invalid or missing API token")
    return raw
