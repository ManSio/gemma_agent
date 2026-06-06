"""Пользовательские багрепорты: ZIP только админам, без вложения пользователю."""

from __future__ import annotations

import os
import threading
import time
from typing import List, Optional, Tuple

from core.admin_module import _admin_acl_ids

_last_user_bug_ts: dict[str, float] = {}
_lock = threading.Lock()


def bug_report_user_submit_enabled() -> bool:
    return (os.getenv("BUG_REPORT_USER_SUBMIT_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on"}


def bug_report_forward_recipient_ids() -> List[str]:
    raw = (os.getenv("BUG_REPORT_FORWARD_USER_IDS") or "").strip()
    if raw:
        return list(dict.fromkeys(x.strip() for x in raw.split(",") if x.strip()))
    return sorted(_admin_acl_ids(), key=lambda x: int(x) if x.isdigit() else 0)


def user_bug_cooldown_seconds() -> int:
    try:
        return max(0, int((os.getenv("BUG_REPORT_USER_COOLDOWN_SEC") or "120").strip()))
    except ValueError:
        return 120


def user_bug_cooldown_ok(user_id: str) -> Tuple[bool, int]:
    """
    True, 0 — можно отправить.
    False, wait_sec — слишком рано.
    """
    uid = (user_id or "").strip()
    if not uid:
        return True, 0
    cd = user_bug_cooldown_seconds()
    if cd <= 0:
        return True, 0
    now = time.time()
    with _lock:
        last = _last_user_bug_ts.get(uid, 0.0)
        elapsed = now - last
        if elapsed < cd:
            return False, max(1, int(cd - elapsed))
        _last_user_bug_ts[uid] = now
    return True, 0


def sanitize_user_bug_args(
    include_net: bool,
    log_n: int,
    log_comp: Optional[str],
    include_full_bundle: bool,
    human_note: Optional[str],
) -> Tuple[bool, int, Optional[str], bool, Optional[str]]:
    """Пользователю не отдаём full bundle; сеть — только если явно разрешена в .env."""
    include_full_bundle = False
    net_ok = (os.getenv("BUG_REPORT_USER_ALLOW_NET") or "").strip().lower() in {"1", "true", "yes", "on"}
    include_net = bool(include_net and net_ok)
    try:
        cap = int((os.getenv("BUG_REPORT_USER_MAX_LOG_LINES") or "50").strip())
    except ValueError:
        cap = 50
    cap = max(15, min(cap, 100))
    log_n = min(int(log_n), cap)
    return include_net, log_n, log_comp, include_full_bundle, human_note
