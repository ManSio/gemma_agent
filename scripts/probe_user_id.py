"""POST_DEPLOY_PROBE_USER_ID / TEST_ADMIN / OWNER для probe-скриптов."""
from __future__ import annotations

import os


def default_probe_user_id() -> str:
    for key in (
        "TEST_ADMIN_TELEGRAM_ID",
        "POST_DEPLOY_PROBE_USER_ID",
        "PROBE_USER_ID",
        "OWNER_TELEGRAM_ID",
    ):
        v = (os.getenv(key) or "").strip()
        if v:
            return v
    return ""
