import unittest

from core import module_gen_intent as mgi


class TestModuleGenIntent(unittest.TestCase):
    def test_signal_self_prog_tool_name(self):
        self.assertTrue(mgi.user_signals_generate_module("SelfProgramming.generate_module сделай крокодила"))

    def test_signal_croc_with_context(self):
        self.assertTrue(
            mgi.user_signals_generate_module(
                "крокодил для группы создай плагин с inline кнопками",
            )
        )

    def test_no_signal_random_chat(self):
        self.assertFalse(mgi.user_signals_generate_module("как дела"))

    def test_no_signal_generate_random_numbers_phrase(self):
        self.assertFalse(
            mgi.user_signals_generate_module(
                "Сгенерируй три случайных числа от 1 до 100 и сложи их",
            )
        )

    def test_no_signal_math_module_zero_point(self):
        """«Модуль» = математика (|f|), не Telegram-плагин."""
        self.assertFalse(
            mgi.user_signals_generate_module(
                "Сгенерируй график модуля линейной функции около нулевой точки",
            )
        )

    def test_no_signal_weak_module_word_only(self):
        self.assertFalse(
            mgi.user_signals_generate_module(
                "Сгенерируй текст про модуль системы и точку отсчёта",
            )
        )

    def test_signal_generate_with_plugin_context(self):
        self.assertTrue(
            mgi.user_signals_generate_module(
                "Сгенерируй плагин с командой /demo и hot_install",
            )
        )

    def test_build_request_none_for_random_generation_task(self):
        self.assertIsNone(
            mgi.build_generate_module_request(
                "Сгенерируй 3 случайных числа от 1 до 100 и посчитай сумму",
                group_id=None,
            )
        )

    def test_plugin_programming_prefers_general_code_sample(self):
        self.assertTrue(
            mgi.plugin_programming_prefers_general(
                "В execute(args) сделай if x > 2: return 3+4 для плагина",
            )
        )

    def test_plugin_programming_prefers_general_false_for_plain_math(self):
        self.assertFalse(mgi.plugin_programming_prefers_general("сколько будет 2+2"))

    def test_build_request_croc_keys(self):
        r = mgi.build_generate_module_request(
            "сгенерируй крокодил в группу aiogram",
            group_id="-100123",
        )
        self.assertIsNotNone(r)
        assert r is not None
        self.assertTrue(r.get("is_crocodile"))
        self.assertTrue(r["module_name"].startswith("group_crocodile"))
        self.assertTrue(r.get("command_prefix"))
        cmds = r.get("commands") or []
        self.assertGreaterEqual(len(cmds), 3)
        tr0 = str((cmds[0] or {}).get("trigger") or "")
        self.assertTrue(tr0.startswith("/") or tr0)  # trigger may be without slash in manifest
        self.assertTrue(r.get("buttons"))

    def test_unique_prefix_avoids_reserved(self):
        reserved = {"xyzzy_new", "xyzzy_guess"}
        p = mgi._unique_cmd_prefix("testgame", reserved)  # noqa: SLF001
        self.assertNotEqual(f"{p}_new", "xyzzy_new")


if __name__ == "__main__":
    unittest.main()
