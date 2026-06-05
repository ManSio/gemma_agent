"""Unit-тесты admin_ops_notify (квота/баланс OpenRouter → ЛС админу)."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.admin_ops_notify import (
    classify_openrouter_issue,
    maybe_notify_openrouter_quota,
    quota_dm_enabled,
    register_admin_ops_bot,
)
from core.openrouter_provider import is_openrouter_quota_or_billing_error


@pytest.mark.parametrize(
    "status,err,expected",
    [
        (402, "Payment Required", True),
        (403, "Key limit exceeded (monthly limit)", True),
        (403, "Forbidden", False),
        (200, "insufficient credits on account", True),
        (429, "rate limit", False),
    ],
)
def test_is_openrouter_quota_or_billing_error(status, err, expected):
    assert is_openrouter_quota_or_billing_error(status, err) is expected


def test_classify_openrouter_issue():
    assert classify_openrouter_issue(403, "monthly limit") == "billing_quota"
    assert classify_openrouter_issue(429, "rate limit exceeded") == "rate_limit"
    assert classify_openrouter_issue(500, "internal error") is None


def test_maybe_notify_skips_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("ADMIN_QUOTA_DM_ENABLED", "false")
    ck = tmp_path / "admin_quota_dm_checkpoint.json"
    monkeypatch.setenv("RESILIENCE_RUNTIME_DIR", str(tmp_path))
    assert maybe_notify_openrouter_quota(
        http_status=403,
        error_text="Key limit exceeded",
        model="deepseek/test",
    ) is False
    assert not ck.exists()


def test_maybe_notify_schedules_dm(monkeypatch, tmp_path):
    monkeypatch.setenv("ADMIN_QUOTA_DM_ENABLED", "true")
    monkeypatch.setenv("ADMIN_NOTIFY_USER_IDS", "999")
    monkeypatch.setenv("ADMIN_QUOTA_DM_COOLDOWN_SEC", "3600")
    monkeypatch.setenv("RESILIENCE_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("BOT_INSTANCE_ID", "test-lan")

    bot = MagicMock()
    bot.send_message = AsyncMock()
    register_admin_ops_bot(bot)

    async def _run() -> None:
        spawned = []

        def _fake_spawn(coro, *, label, loop=None):
            spawned.append(label)
            return asyncio.get_running_loop().create_task(coro)

        with patch("core.async_spawn.spawn_logged", side_effect=_fake_spawn):
            ok = maybe_notify_openrouter_quota(
                http_status=403,
                error_text="Key limit exceeded (monthly limit)",
                model="deepseek/deepseek-v4-flash",
                fallback_model="openrouter/free",
            )
            assert ok is True
            assert spawned == ["admin_quota_dm"]
            await asyncio.sleep(0)
            bot.send_message.assert_awaited()
            _args, kwargs = bot.send_message.await_args
            assert kwargs["chat_id"] == 999
            assert "OpenRouter" in kwargs["text"]
            assert "openrouter/free" in kwargs["text"]

    asyncio.run(_run())

    ck_path = tmp_path / "admin_quota_dm_checkpoint.json"
    assert ck_path.is_file()
    data = json.loads(ck_path.read_text(encoding="utf-8"))
    assert data.get("sent")


def test_maybe_notify_respects_cooldown(monkeypatch, tmp_path):
    monkeypatch.setenv("ADMIN_QUOTA_DM_ENABLED", "true")
    monkeypatch.setenv("ADMIN_NOTIFY_USER_IDS", "999")
    monkeypatch.setenv("ADMIN_QUOTA_DM_COOLDOWN_SEC", "3600")
    monkeypatch.setenv("RESILIENCE_RUNTIME_DIR", str(tmp_path))

    bot = MagicMock()
    bot.send_message = AsyncMock()
    register_admin_ops_bot(bot)

    async def _run() -> None:
        with patch("core.async_spawn.spawn_logged") as spawn:
            assert maybe_notify_openrouter_quota(
                http_status=403,
                error_text="Key limit exceeded (monthly limit)",
                model="m1",
            ) is True
            assert maybe_notify_openrouter_quota(
                http_status=403,
                error_text="Key limit exceeded (monthly limit)",
                model="m1",
            ) is False
            assert spawn.call_count == 1

    asyncio.run(_run())


def test_quota_dm_enabled_default_true(monkeypatch):
    monkeypatch.delenv("ADMIN_QUOTA_DM_ENABLED", raising=False)
    assert quota_dm_enabled() is True
