"""Общая авторизация HTTP API (api.py, core/api_ops.py)."""
from __future__ import annotations

import hmac
import logging
import os
from typing import Optional, Set

from fastapi import Header, HTTPException, Query

logger = logging.getLogger(__name__)

DEFAULT_API_TOKEN = "your_secure_api_token_here"
_PRODUCTION_APP_ENVS = frozenset({"production", "prod"})
_API_ENABLED_TRUTHY = frozenset({"1", "true", "yes", "on"})


def normalize_api_token(raw: Optional[str]) -> str:
    return (raw or "").strip().strip('"').strip("'")


def is_default_api_token(token: str) -> bool:
    """True when API_TOKEN is unset and the repo placeholder is in use."""
    return normalize_api_token(token) == DEFAULT_API_TOKEN


def is_production_app_env() -> bool:
    """Production deploy flag from APP_ENV (see .env.example)."""
    return (os.getenv("APP_ENV") or "development").strip().lower() in _PRODUCTION_APP_ENVS


def is_api_enabled_from_env() -> bool:
    """Whether HTTP API is enabled via API_ENABLED in .env."""
    return (os.getenv("API_ENABLED") or "false").strip().lower() in _API_ENABLED_TRUTHY


def enforce_startup_api_token_config(token: str) -> None:
    """Refuse API startup with placeholder token when API is enabled or APP_ENV is production."""
    if not is_default_api_token(token):
        return
    api_on = is_api_enabled_from_env()
    prod = is_production_app_env()
    if prod or api_on:
        if api_on and not prod:
            detail = (
                "API_ENABLED=true but API_TOKEN is unset — set a strong API_TOKEN in .env "
                "or set API_ENABLED=false for bot-only mode."
            )
        else:
            detail = (
                "API_TOKEN must be set in .env for production (APP_ENV=production). "
                "Refusing to start with the default placeholder token."
            )
        logger.critical(
            "Refusing API startup with default API_TOKEN (api_enabled=%s app_env=%s)",
            api_on,
            os.getenv("APP_ENV") or "development",
            extra={"gemma_event": "api_token_default_blocked"},
        )
        raise SystemExit(detail)
    logger.warning(
        "Using default API token — allowed only when API_ENABLED=false; set API_TOKEN in .env",
        extra={"gemma_event": "api_token_default_dev"},
    )


def allowed_api_tokens() -> Set[str]:
    primary = normalize_api_token(os.getenv("API_TOKEN", DEFAULT_API_TOKEN))
    relay = normalize_api_token(os.getenv("BOT_RELAY_API_TOKEN", ""))
    out = {primary}
    if relay:
        out.add(relay)
    return out


def token_matches_allowed(candidate: str, allowed: Optional[Set[str]] = None) -> bool:
    """Timing-safe compare of API token against configured secrets."""
    cand = normalize_api_token(candidate)
    if not cand:
        return False
    tokens = allowed if allowed is not None else allowed_api_tokens()
    for expected in tokens:
        exp = normalize_api_token(expected)
        if exp and hmac.compare_digest(cand, exp):
            return True
    return False


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
    if not raw or not token_matches_allowed(raw):
        raise HTTPException(status_code=401, detail="Invalid or missing API token")
    return raw
