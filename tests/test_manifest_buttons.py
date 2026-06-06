import unittest
from unittest.mock import MagicMock

from core.manifest_buttons import (
    manifest_buttons_keyboard,
    merge_manifest_buttons_keyboards,
    parse_mbtn_callback,
    resolve_button_simulated_text,
)


class ManifestButtonsTests(unittest.TestCase):
    def test_parse_mbtn(self):
        self.assertEqual(parse_mbtn_callback("mbtn:echo:ECHO_DEMO"), ("echo", "ECHO_DEMO"))
        self.assertIsNone(parse_mbtn_callback("help:main"))

    def test_keyboard_build(self):
        kb = manifest_buttons_keyboard(
            "echo",
            [{"name": "X", "label": "Lbl"}],
        )
        self.assertIsNotNone(kb)
        self.assertTrue(kb.inline_keyboard)

    def test_resolve_simulate_text(self):
        reg = MagicMock()
        mod = MagicMock()
        mod.manifest = MagicMock()
        mod.manifest.buttons = [{"name": "ECHO_DEMO", "simulate_text": "/echo hi"}]
        reg.loaded_modules = {"echo": mod}
        self.assertEqual(resolve_button_simulated_text(reg, "echo", "ECHO_DEMO"), "/echo hi")

    def test_merge_keyboards_chat_plus_echo(self):
        reg = MagicMock()
        orch = MagicMock()
        orch.manifest = MagicMock()
        orch.manifest.buttons = []
        echo_mod = MagicMock()
        echo_mod.manifest = MagicMock()
        echo_mod.manifest.buttons = [{"name": "ECHO_DEMO", "label": "Эхо", "simulate_text": "/echo x"}]
        reg.loaded_modules = {"chat-orchestrator": orch, "echo": echo_mod}
        kb = merge_manifest_buttons_keyboards(reg, ["chat-orchestrator", "echo"])
        self.assertIsNotNone(kb)
        self.assertEqual(len(kb.inline_keyboard), 1)


if __name__ == "__main__":
    unittest.main()
