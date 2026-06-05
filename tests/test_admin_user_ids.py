"""admin_user_ids export for bug reports and notifications."""

from __future__ import annotations

import os
from unittest.mock import patch

from core.admin_module import admin_user_ids


def test_admin_user_ids_from_env():
    with patch.dict(os.environ, {"ADMIN_USER_IDS": "123,456", "ADMIN_NOTIFY_USER_IDS": ""}, clear=False):
        ids = admin_user_ids()
    assert 123 in ids
    assert 456 in ids
