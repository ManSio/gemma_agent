import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from core.autopilot_cycle import _maybe_llm_triage_anomalies


def test_llm_triage_skips_when_disabled(monkeypatch) -> None:
    monkeypatch.setenv("AUTOPILOT_LLM_TRIAGE_ENABLED", "false")
    bot = MagicMock()
    bot.send_message = AsyncMock()

    async def run():
        with patch("core.brain.call_brain", new_callable=AsyncMock) as cb:
            await _maybe_llm_triage_anomalies(
                MagicMock(),
                bot,
                {"anomalies": [{"code": "x"}]},
                ["rec"],
                [],
            )
        cb.assert_not_called()

    asyncio.run(run())
    bot.send_message.assert_not_called()


def test_llm_triage_calls_brain_when_enabled(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AUTOPILOT_LLM_TRIAGE_ENABLED", "true")
    monkeypatch.setenv("AUTOPILOT_LLM_TRIAGE_COOLDOWN_SEC", "0")
    monkeypatch.setenv("RESILIENCE_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("ADMIN_USER_IDS", "999001")
    monkeypatch.delenv("ADMIN_NOTIFY_USER_IDS", raising=False)
    bot = MagicMock()
    bot.send_message = AsyncMock()

    async def fake_brain(user_text, ctx, system_prompt):
        assert ctx.get("brain_disable_tools") is True
        return "Short admin triage hint."

    async def run():
        with patch("core.brain.call_brain", side_effect=fake_brain):
            await _maybe_llm_triage_anomalies(
                MagicMock(),
                bot,
                {"anomalies": [{"code": "slow_boot_path", "detail": "test"}], "pulse": {"k": 1}},
                ["check logs"],
                [],
            )

    asyncio.run(run())
    bot.send_message.assert_called()
    _args, kwargs = bot.send_message.call_args
    assert "Short admin" in kwargs.get("text", "")
