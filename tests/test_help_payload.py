import unittest
from unittest import mock

from core.input_handlers.help_payload import build_help_payload, collect_command_catalog
from core.plugin_registry import PluginRegistry


class HelpPayloadTests(unittest.TestCase):
    def test_collect_empty_registry(self):
        reg = PluginRegistry()
        with mock.patch.object(reg, "get_modules", return_value=[]):
            self.assertEqual(collect_command_catalog(reg), [])

    def test_build_help_admin_overview_and_subpages(self):
        reg = PluginRegistry()
        with mock.patch.object(reg, "get_modules", return_value=[]):
            chunks, kb = build_help_payload(plugin_registry=reg, is_admin=True, page="admin")
        blob = "\n".join(chunks)
        self.assertIn("/admin_system", blob)
        self.assertIn("/admin", blob)
        self.assertNotIn("/admin_governance", blob)
        self.assertIsNotNone(kb)
        flat = [b.callback_data for row in (kb.inline_keyboard or []) for b in row]
        self.assertIn("help:admin_sys", flat)
        self.assertFalse(any(str(x).startswith("ac:") for x in flat))
        self.assertFalse(any(str(x).startswith("ha:") for x in flat))

        with mock.patch.object(reg, "get_modules", return_value=[]):
            chunks_pol, _ = build_help_payload(plugin_registry=reg, is_admin=True, page="admin_pol")
        self.assertIn("/admin_governance", "\n".join(chunks_pol))
        self.assertIn("/admin_logs", "\n".join(build_help_payload(plugin_registry=reg, is_admin=True, page="admin_dev")[0]))
        obs_txt = "\n".join(build_help_payload(plugin_registry=reg, is_admin=True, page="admin_obs")[0])
        self.assertIn("/admin_efficiency", obs_txt)
        self.assertIn("/admin_kv_branches", obs_txt)
        obs2 = "\n".join(build_help_payload(plugin_registry=reg, is_admin=True, page="admin_obs_2")[0])
        self.assertIn("/admin_memory_insight", obs2)
        self.assertIn("/admin_plugins_health", "\n".join(build_help_payload(plugin_registry=reg, is_admin=True, page="admin_dev")[0]))
        self.assertIn("/admin_bug help", "\n".join(build_help_payload(plugin_registry=reg, is_admin=True, page="admin_net")[0]))

    def test_admin_help_nav_has_no_run_callbacks(self):
        from core.input_handlers.help_payload import admin_help_section_nav_rows

        flat = [b.callback_data for row in admin_help_section_nav_rows() for b in row]
        self.assertTrue(all(str(x).startswith("help:admin") for x in flat))

    def test_user_more_page_has_full_list(self):
        reg = PluginRegistry()
        with mock.patch.object(reg, "get_modules", return_value=[]):
            chunks, _ = build_help_payload(plugin_registry=reg, is_admin=False, page="user_more")
        blob = "\n".join(chunks)
        self.assertIn("/corpus_books", blob)
        self.assertIn("/zip_read", blob)

    def test_user_page_compact(self):
        reg = PluginRegistry()
        with mock.patch.object(reg, "get_modules", return_value=[]):
            chunks, kb = build_help_payload(plugin_registry=reg, is_admin=False, page="user")
        blob = "\n".join(chunks)
        self.assertIn("/start", blob)
        self.assertNotIn("/corpus_books", blob)
        flat = [b.callback_data for row in (kb.inline_keyboard or []) for b in row]
        self.assertIn("help:user_more", flat)

    def test_build_help_non_admin_admin_page(self):
        reg = PluginRegistry()
        with mock.patch.object(reg, "get_modules", return_value=[]):
            chunks, _ = build_help_payload(plugin_registry=reg, is_admin=False, page="admin")
        self.assertTrue(any("администраторам" in c.lower() for c in chunks))

    def test_modules_page_pagination_header(self):
        reg = PluginRegistry()

        class _M:
            manifest = mock.Mock(name="bigmod", commands=[{"trigger": f"/c{i}", "description": ""} for i in range(25)])

        mods = [_M() for _ in range(1)]
        with mock.patch.object(reg, "get_modules", return_value=mods):
            chunks, _ = build_help_payload(plugin_registry=reg, is_admin=False, page="modules_1")
        blob = "\n".join(chunks)
        self.assertIn("стр. 1/", blob)
        self.assertIn("Плагины", blob)
        self.assertIn("/c0", blob)

    def test_modules_page_empty_catalog_hint(self):
        reg = PluginRegistry()
        with mock.patch.object(reg, "get_modules", return_value=[]):
            chunks, _ = build_help_payload(plugin_registry=reg, is_admin=False, page="modules_1")
        blob = "\n".join(chunks)
        self.assertIn("/system_state", blob)
        self.assertIn("/status", blob)
        self.assertIn("пуст", blob.lower())

    def test_modules_help_has_run_buttons(self):
        reg = PluginRegistry()

        class _M:
            manifest = mock.Mock(name="demo", commands=[{"trigger": "/ping_demo", "description": "test"}])

        with mock.patch.object(reg, "get_modules", return_value=[_M()]):
            _, kb = build_help_payload(plugin_registry=reg, is_admin=False, page="modules_1")
        self.assertIsNotNone(kb)
        flat = [b.callback_data for row in (kb.inline_keyboard or []) for b in row]
        self.assertTrue(any(x and str(x).startswith("hc:") for x in flat))

    def test_user_page_has_quick_buttons(self):
        reg = PluginRegistry()
        with mock.patch.object(reg, "get_modules", return_value=[]):
            chunks, kb = build_help_payload(plugin_registry=reg, is_admin=False, page="user")
        blob = "\n".join(chunks)
        self.assertIn("/explain", blob)
        self.assertIn("/solve", blob)
        self.assertIn("/check", blob)
        self.assertIn("/quiz", blob)
        flat = [b.callback_data for row in (kb.inline_keyboard or []) for b in row]
        self.assertTrue(any(str(x).startswith("hu:") for x in flat))

    def test_admin_nav_has_patches(self):
        reg = PluginRegistry()
        with mock.patch.object(reg, "get_modules", return_value=[]):
            _, kb = build_help_payload(plugin_registry=reg, is_admin=True, page="main")
        flat = [b.callback_data for row in (kb.inline_keyboard or []) for b in row]
        self.assertIn("help:patches", flat)

    def test_patches_page_lists_commands(self):
        reg = PluginRegistry()
        with mock.patch.object(reg, "get_modules", return_value=[]):
            chunks, kb = build_help_payload(plugin_registry=reg, is_admin=True, page="patches")
        blob = "\n".join(chunks)
        self.assertIn("/remember_patch", blob)
        self.assertIn("/clear_all_patches", blob)
        self.assertIn("/list_patches", blob)
        flat = [b.callback_data for row in (kb.inline_keyboard or []) for b in row]
        self.assertTrue(any(str(x).startswith("hp:") for x in flat))


if __name__ == "__main__":
    unittest.main()
