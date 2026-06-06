"""Тесты Heal Executor — реальное применение шагов лечения."""
import os
import unittest
from unittest.mock import patch, AsyncMock

from core.heal_executor import parse_steps, apply_steps


class TestParseSteps(unittest.TestCase):
    def test_parse_disable_module(self):
        steps = ["/admin_plugin_disable broken_mod"]
        parsed = parse_steps(steps)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["action"], "disable_module")
        self.assertEqual(parsed[0]["module"], "broken_mod")

    def test_parse_enable_module_command(self):
        steps = ["/admin_plugin_enable fixed_mod"]
        parsed = parse_steps(steps)
        self.assertEqual(parsed[0]["action"], "enable_module")
        self.assertEqual(parsed[0]["module"], "fixed_mod")

    def test_parse_env_allowed(self):
        steps = ["env HEALER_MODULE_MAX_FAILURES=5"]
        parsed = parse_steps(steps)
        self.assertEqual(parsed[0]["action"], "set_env")
        self.assertEqual(parsed[0]["key"], "HEALER_MODULE_MAX_FAILURES")
        self.assertEqual(parsed[0]["value"], "5")

    def test_parse_env_blocked(self):
        steps = ["env TELEGRAM_TOKEN=123"]
        parsed = parse_steps(steps)
        self.assertEqual(parsed[0]["action"], "set_env_blocked")

    def test_parse_reset_module_failures(self):
        steps = ["reset module failures my_mod"]
        parsed = parse_steps(steps)
        self.assertEqual(parsed[0]["action"], "reset_module_failures")
        self.assertEqual(parsed[0]["module"], "my_mod")

    def test_parse_reset_error_counters(self):
        steps = ["reset error counters"]
        parsed = parse_steps(steps)
        self.assertEqual(parsed[0]["action"], "reset_error_counters")

    def test_parse_restart_container(self):
        steps = ["restart container"]
        parsed = parse_steps(steps)
        self.assertEqual(parsed[0]["action"], "restart_container")

    def test_parse_ephemeral_patch(self):
        steps = ["ephemeral patch: module_name || Don't use this module"]
        parsed = parse_steps(steps)
        self.assertEqual(parsed[0]["action"], "create_ephemeral_patch")
        self.assertEqual(parsed[0]["trigger"], "module_name")
        self.assertEqual(parsed[0]["instruction"], "Don't use this module")

    def test_parse_exit_safe_mode(self):
        steps = ["exit safe mode"]
        parsed = parse_steps(steps)
        self.assertEqual(parsed[0]["action"], "exit_safe_mode")

    def test_parse_clear_safe_mode(self):
        steps = ["clear safe mode and restart"]
        parsed = parse_steps(steps)
        self.assertEqual(parsed[0]["action"], "exit_safe_mode")

    def test_parse_multi_step(self):
        steps = [
            "/admin_plugin_disable bad_mod",
            "reset module failures bad_mod",
            "env HEALER_MODULE_MAX_FAILURES=5",
        ]
        parsed = parse_steps(steps)
        self.assertEqual(len(parsed), 3)
        self.assertEqual(parsed[0]["action"], "disable_module")
        self.assertEqual(parsed[1]["action"], "reset_module_failures")
        self.assertEqual(parsed[2]["action"], "set_env")

    def test_parse_unknown_step(self):
        steps = ["do something weird 123"]
        parsed = parse_steps(steps)
        self.assertEqual(parsed[0]["action"], "unknown")

    def test_empty_steps(self):
        parsed = parse_steps([])
        self.assertEqual(parsed, [])

    def test_parse_disable_module_text(self):
        steps = ["disable module broken_mod"]
        parsed = parse_steps(steps)
        self.assertEqual(parsed[0]["action"], "disable_module")
        self.assertEqual(parsed[0]["module"], "broken_mod")


class TestApplySteps(unittest.TestCase):
    def test_apply_empty_steps(self):
        import asyncio
        result = asyncio.run(apply_steps([], reason="test"))
        self.assertTrue(result["ok"])

    def test_apply_set_env(self):
        import asyncio
        old = os.environ.get("HEALER_MODULE_MAX_FAILURES", "")
        result = asyncio.run(apply_steps(["env HEALER_MODULE_MAX_FAILURES=7"],
                                           reason="test"))
        self.assertTrue(result["ok"])
        self.assertEqual(os.environ["HEALER_MODULE_MAX_FAILURES"], "7")
        # restore
        if old:
            os.environ["HEALER_MODULE_MAX_FAILURES"] = old
        else:
            os.environ.pop("HEALER_MODULE_MAX_FAILURES", None)

    def test_apply_blocked_env(self):
        import asyncio
        result = asyncio.run(apply_steps(["env TELEGRAM_TOKEN=hack"],
                                           reason="test"))
        self.assertFalse(result["ok"])

    def test_apply_unknown_step(self):
        import asyncio
        result = asyncio.run(apply_steps(["garbage step"], reason="test"))
        self.assertFalse(result["ok"])

    @patch("core.heal_executor._exec_disable_module", new_callable=AsyncMock)
    def test_apply_disable_module(self, mock_exec):
        mock_exec.return_value = {"ok": True}
        import asyncio
        result = asyncio.run(apply_steps(["/admin_plugin_disable bad_mod"],
                                           reason="test"))
        self.assertTrue(result["ok"])
